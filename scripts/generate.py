#!/usr/bin/env python3
"""Generates the typed parts of the client from the Handles API OpenAPI spec.

    python scripts/generate.py [path-or-url-to-openapi.json]

Writes src/handlesapi/_types.py, _operations.py and _methods.py. The default source is the public
spec repository. The generated files are committed, so installing the client needs no generation step.
Standard library only.
"""
from __future__ import annotations

import json
import keyword
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SPEC = "https://handlesapi.com/openapi.json"
OUT = Path(__file__).resolve().parent.parent / "src" / "handlesapi"


def load(source: str) -> dict[str, Any]:
    if re.match(r"^https?:", source):
        with urllib.request.urlopen(source, timeout=60) as r:  # noqa: S310 - fixed https source
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def pascal(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", s) if p)


def snake_op(operation_id: str) -> str:
    parts = [p for p in re.split(r"[.\-_]", operation_id) if p]
    return "_".join(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", p).lower() for p in parts)


def first_sentence(text: str | None, limit: int = 160) -> str:
    t = re.sub(r"\s+", " ", (text or "").split("\n\n")[0]).strip().replace('"""', "'''").replace("\\", "/")
    if len(t) <= limit:
        return t
    cut = t[:limit]
    return re.sub(r"\s+\S*$", "", cut) + "..."


class Types:
    """Collects TypedDict classes for response schemas."""

    def __init__(self) -> None:
        self.blocks: list[str] = []
        self.names: set[str] = set()

    def unique(self, name: str) -> str:
        base, i = name, 2
        while name in self.names:
            name = f"{base}{i}"
            i += 1
        self.names.add(name)
        return name

    def type_of(self, schema: dict[str, Any] | None, hint: str) -> str:
        """Python annotation (as a string) for a response schema, emitting nested TypedDicts."""
        if not schema:
            return "Any"
        if "$ref" in schema:
            return schema["$ref"].split("/")[-1]
        if "enum" in schema:
            return "Literal[" + ", ".join(json.dumps(v) for v in schema["enum"]) + "]"
        types = schema.get("type")
        types = types if isinstance(types, list) else [types]
        nullable = "null" in types or schema.get("nullable") is True
        t = next((x for x in types if x and x != "null"), None)
        if t == "string":
            base = "str"
        elif t == "integer":
            base = "int"
        elif t == "number":
            base = "float"
        elif t == "boolean":
            base = "bool"
        elif t == "array":
            base = f"List[{self.type_of(schema.get('items'), hint + 'Item')}]"
        elif t == "object" or (t is None and "properties" in schema):
            if "properties" in schema:
                base = self.emit_typed_dict(hint, schema)
            elif isinstance(schema.get("additionalProperties"), dict):
                base = f"Dict[str, {self.type_of(schema['additionalProperties'], hint + 'Value')}]"
            else:
                base = "Dict[str, Any]"
        else:
            base = "Any"
        return f"Optional[{base}]" if nullable and base != "Any" else base

    def emit_typed_dict(self, hint: str, schema: dict[str, Any]) -> str:
        name = self.unique(hint)
        required = set(schema.get("required", []))
        fields: list[tuple[str, str, str, bool]] = []
        for key, sub in schema["properties"].items():
            ann = self.type_of(sub, name + pascal(key))
            nullable_schema = isinstance(sub.get("type"), list) and "null" in sub["type"]
            if key not in required and not nullable_schema and ann != "Any" and not ann.startswith("Optional["):
                ann = f"Optional[{ann}]"
            fields.append((key, ann, first_sentence(sub.get("description"), 200), key in required))
        self.blocks.append(self.render(name, fields))
        return name

    @staticmethod
    def render(name: str, fields: list[tuple[str, str, str, bool]]) -> str:
        invalid = any(keyword.iskeyword(k) or not k.isidentifier() for k, *_ in fields)
        req = [f for f in fields if f[3]]
        opt = [f for f in fields if not f[3]]
        if invalid:
            # Functional syntax: a key such as "from" is a keyword and cannot be a class attribute.
            def entries(fs: list[tuple[str, str, str, bool]]) -> str:
                return ", ".join(f"{json.dumps(k)}: {json.dumps(a)}" for k, a, *_ in fs)

            if req:
                raise SystemExit(f"{name}: required keys together with a keyword key are not supported")
            return f"{name} = TypedDict(\"{name}\", {{{entries(opt)}}}, total=False)\n"

        def body(fs: list[tuple[str, str, str, bool]]) -> str:
            if not fs:
                return "    pass\n"
            out = []
            for k, a, d, _ in fs:
                out.append(f"    {k}: {a}\n")
                if d:
                    out.append(f'    """{d}"""\n')
            return "".join(out)

        if req:
            return (
                f"class _{name}Required(TypedDict):\n{body(req)}\n\n"
                f"class {name}(_{name}Required, total=False):\n{body(opt)}"
            )
        return f"class {name}(TypedDict, total=False):\n{body(opt)}"


def param_type(schema: dict[str, Any] | None) -> str:
    """Annotation for a request parameter. Arrays and objects stay loose so callers can pass plain dicts."""
    if not schema:
        return "Any"
    if "enum" in schema:
        return "Literal[" + ", ".join(json.dumps(v) for v in schema["enum"]) + "]"
    t = schema.get("type")
    t = next((x for x in t if x != "null"), None) if isinstance(t, list) else t
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "Sequence[Any]",
        "object": "Mapping[str, Any]",
    }.get(t or "", "Any")


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SPEC
    spec = load(source)
    version = spec["info"]["version"]
    banner = f'"""Generated by scripts/generate.py from the Handles API OpenAPI spec (v{version}). Do not edit by hand."""\n'

    types = Types()
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name == "ApiError":
            continue  # raised as HandlesApiError
        if name == "ApiErrorCode":
            types.names.add(name)
            types.blocks.append(
                "ApiErrorCode = Literal[\n" + "".join(f"    {json.dumps(v)},\n" for v in schema["enum"]) + "]\n"
            )
        else:
            types.names.add(name)
    # Emit the schema objects after names are reserved so $ref targets exist.
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in ("ApiError", "ApiErrorCode"):
            continue
        types.names.discard(name)
        types.emit_typed_dict(name, schema)

    ops: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, item in spec["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if not op:
                continue
            name = snake_op(op["operationId"])
            if name in seen:
                raise SystemExit(f"duplicate method name {name}")
            seen.add(name)
            pascal_name = pascal(name)
            params: list[dict[str, Any]] = []
            for p in op.get("parameters", []):
                params.append(
                    {
                        "name": p["name"],
                        "in": p["in"],
                        "required": bool(p.get("required")),
                        "type": param_type(p.get("schema")),
                        "doc": first_sentence(p.get("description")),
                    }
                )
            body_schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body_schema and "properties" in body_schema:
                breq = set(body_schema.get("required", []))
                for n, s in body_schema["properties"].items():
                    params.append(
                        {
                            "name": n,
                            "in": "body",
                            "required": n in breq,
                            "type": param_type(s),
                            "doc": first_sentence(s.get("description")),
                        }
                    )
            data_schema = (
                op["responses"]["200"]["content"]["application/json"]["schema"].get("properties", {}).get("data")
            )
            data_type = types.type_of(data_schema, pascal_name + "Data")
            if data_type != pascal_name + "Data":
                # Not an object schema: alias it so every operation has a named Data type.
                types.blocks.append(f"{pascal_name}Data = {data_type}\n")
            ops.append(
                {
                    "name": name,
                    "Data": pascal_name + "Data",
                    "method": method.upper(),
                    "path": path,
                    "tag": (op.get("tags") or ["Other"])[0],
                    "summary": op.get("summary", ""),
                    "credits": op.get("x-credits"),
                    "live": bool(op.get("x-live-call")),
                    "scope": op.get("x-scope"),
                    "params": params,
                }
            )

    # ---- _types.py ----
    head = (
        banner
        + "from __future__ import annotations\n\n"
        + "from typing import Any, Dict, List, Literal, Optional, TypedDict\n\n\n"
    )
    (OUT / "_types.py").write_text(head + "\n\n".join(types.blocks) + "\n", encoding="utf-8")

    # ---- _operations.py ----
    lines = [
        banner,
        "from __future__ import annotations\n",
        "from typing import Dict, NamedTuple, Optional, Tuple\n\n",
        "class Operation(NamedTuple):\n",
        "    method: str\n    path: str\n    tag: str\n    summary: str\n",
        "    credits: Optional[int]\n    live: bool\n    scope: Optional[str]\n",
        "    path_params: Tuple[str, ...]\n    query_params: Tuple[str, ...]\n    body_params: Tuple[str, ...]\n",
        "    paginated: bool\n\n\n",
        "OPERATIONS: Dict[str, Operation] = {\n",
    ]
    for o in ops:
        by = lambda where: tuple(p["name"] for p in o["params"] if p["in"] == where)  # noqa: E731
        q = by("query")
        lines.append(
            f"    {json.dumps(o['name'])}: Operation({o['method']!r}, {o['path']!r}, {o['tag']!r}, "
            f"{o['summary']!r}, {o['credits']!r}, {o['live']!r}, {o['scope']!r}, "
            f"{by('path')!r}, {q!r}, {by('body')!r}, {'cursor' in q!r}),\n"
        )
    lines.append("}\n")
    (OUT / "_operations.py").write_text("".join(lines), encoding="utf-8")

    # ---- _methods.py ----
    used = sorted(o["Data"] for o in ops)
    m = [
        banner,
        "# ruff: noqa: E501\n",
        "from __future__ import annotations\n\n",
        "from typing import Any, Literal, Mapping, Optional, Sequence\n\n",
        "from ._core import Response\n",
        "from ._types import (\n" + "".join(f"    {n},\n" for n in used) + ")\n\n\n",
    ]
    for cls, is_async in (("SyncOperations", False), ("AsyncOperations", True)):
        m.append(f"class {cls}:\n")
        m.append(
            "    \"\"\"One typed method per endpoint. Mixed into the client; see `HandlesApi`.\"\"\"\n\n"
            "    def _op(self, name: str, args: Mapping[str, Any], timeout: Optional[float] = None) -> Any:\n"
            "        raise NotImplementedError\n\n"
            if not is_async
            else "    \"\"\"One typed method per endpoint. Mixed into the client; see `AsyncHandlesApi`.\"\"\"\n\n"
            "    async def _op(self, name: str, args: Mapping[str, Any], timeout: Optional[float] = None) -> Any:\n"
            "        raise NotImplementedError\n\n"
        )
        for o in ops:
            req = [p for p in o["params"] if p["required"]]
            opt = [p for p in o["params"] if not p["required"]]
            sig = ["self"] + [f"{p['name']}: {p['type']}" for p in req]
            sig.append("*")
            sig += [f"{p['name']}: Optional[{p['type']}] = None" for p in opt]
            sig.append("timeout: Optional[float] = None")
            kw = "async def" if is_async else "def"
            await_ = "await " if is_async else ""
            credits = f" {o['credits']} credit{'s' if o['credits'] != 1 else ''}{', live read' if o['live'] else ''}." if o["credits"] is not None else ""
            doc = [f"{first_sentence(o['summary'], 200)}.".replace("..", "."), "", f"`{o['method']} {o['path']}`.{credits}"]
            if o["params"]:
                doc += ["", "Args:"]
                doc += [f"    {p['name']}: {p['doc']}" if p["doc"] else f"    {p['name']}." for p in o["params"]]
            doc_text = "\n        ".join(doc)
            args = ", ".join(f"{json.dumps(p['name'])}: {p['name']}" for p in o["params"])
            m.append(f"    {kw} {o['name']}(\n        " + ",\n        ".join(sig) + f",\n    ) -> Response[{o['Data']}]:\n")
            m.append(f'        """{doc_text}\n        """\n')
            m.append(f"        return {await_}self._op({json.dumps(o['name'])}, {{{args}}}, timeout)  # type: ignore[no-any-return]\n\n")
        m.append("\n")
    (OUT / "_methods.py").write_text("".join(m).rstrip() + "\n", encoding="utf-8")
    paginated = sum(1 for o in ops if any(p["name"] == "cursor" for p in o["params"]))
    print(f"generated {len(ops)} operations ({paginated} paginated) from {source}")


if __name__ == "__main__":
    main()
