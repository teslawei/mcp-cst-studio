"""STP-side (OCC) model simplification tools.

The OCC pipeline does the geometry work that CST/ACIS handles poorly:
component pruning by product-path whitelist, fuzzy boolean fuse of mating
sections, and validity-checked export.  CST then only does what CST owns:
import, material assignment, mesh, solve.

Requires the optional ``cadquery-ocp`` dependency (OpenCascade bindings):

    pip install cadquery-ocp

These tools are pure file processing -- no running CST is needed.
"""
import json
import os
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from ..validators import validate_file_path

if TYPE_CHECKING:
    from mcp_cst_studio.cst_client import CSTClient

try:  # optional dependency: offline servers may not have OCP
    import OCP  # noqa: F401

    _HAS_OCP = True
except Exception:  # pragma: no cover - depends on env
    _HAS_OCP = False


TOOLS: list[Tool] = [
    Tool(
        name="cst_stp_recon",
        description=(
            "Enumerate the product/solid structure of a STEP file with exact "
            "bounding boxes (OpenCascade XCAF). Read-only reconnaissance for "
            "building a pruning whitelist. Requires cadquery-ocp."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_stp": {
                    "type": "string",
                    "description": "Path to the source .stp/.step file.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Max solids to report (default 200).",
                },
            },
            "required": ["source_stp"],
        },
    ),
    Tool(
        name="cst_stp_simplify",
        description=(
            "OCC model-simplification pipeline: keep only solids under the "
            "whitelisted product paths, ShapeFix inputs, fuzzy-fuse selected "
            "roles into one solid, validity-check every stage, and export a "
            "clean AP214 STP. This replaces hundreds of CST-side component "
            "deletions and the boolean ops ACIS refuses. Requires "
            "cadquery-ocp."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_stp": {
                    "type": "string",
                    "description": "Path to the source .stp/.step file.",
                },
                "output_stp": {
                    "type": "string",
                    "description": "Path of the simplified STP to write.",
                },
                "whitelist": {
                    "type": "object",
                    "description": (
                        "Map of role name -> product-path suffix "
                        "(underscore-joined product names, e.g. "
                        "'6543210-00-A_0_6543211-00-A_1_2082726-00-A'). "
                        "Only solids under these paths are kept."
                    ),
                },
                "fuse_roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Role names whose solids are fuzzy-fused into one "
                        "solid (e.g. ['housing']). Other roles are kept "
                        "as-is."
                    ),
                },
                "cache_dir": {
                    "type": "string",
                    "description": (
                        "Optional directory to cache intermediate .brep "
                        "files for fast re-runs."
                    ),
                },
                "fuzzy": {
                    "type": "number",
                    "description": "Fuzzy value in mm for the fuse (default 1e-4).",
                },
            },
            "required": ["source_stp", "output_stp", "whitelist"],
        },
    ),
]


# ---------------------------------------------------------------------------
# OCC helpers (imported lazily so offline mode works without OCP)
# ---------------------------------------------------------------------------


def _ocp():
    """Import and return the OCP submodules used by the pipeline."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepTools import BRepTools
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.collections import Sequence_TDF_Label

    return dict(
        Bnd_Box=Bnd_Box,
        BRep_Builder=BRep_Builder,
        BRepAlgoAPI_Fuse=BRepAlgoAPI_Fuse,
        BRepBndLib=BRepBndLib,
        BRepCheck_Analyzer=BRepCheck_Analyzer,
        BRepGProp=BRepGProp,
        BRepTools=BRepTools,
        GProp_GProps=GProp_GProps,
        IFSelect_RetDone=IFSelect_RetDone,
        Interface_Static=Interface_Static,
        ShapeFix_Shape=ShapeFix_Shape,
        STEPCAFControl_Reader=STEPCAFControl_Reader,
        STEPCAFControl_Writer=STEPCAFControl_Writer,
        TCollection_ExtendedString=TCollection_ExtendedString,
        TDataStd_Name=TDataStd_Name,
        TDocStd_Document=TDocStd_Document,
        TopAbs_ShapeEnum=TopAbs_ShapeEnum,
        TopExp_Explorer=TopExp_Explorer,
        TopoDS_Compound=TopoDS_Compound,
        TopoDS_Shape=TopoDS_Shape,
        XCAFDoc_DocumentTool=XCAFDoc_DocumentTool,
        Sequence_TDF_Label=Sequence_TDF_Label,
    )


def _solids_of(o, shape):
    res = []
    exp = o["TopExp_Explorer"](shape, o["TopAbs_ShapeEnum"].TopAbs_SOLID)
    while exp.More():
        res.append(exp.Current())
        exp.Next()
    return res


def _volume(o, shape):
    try:
        props = o["GProp_GProps"]()
        o["BRepGProp"].VolumeProperties_s(shape, props)
        return props.Mass()
    except Exception:
        return float("nan")


def _bbox(o, shape):
    box = o["Bnd_Box"]()
    try:
        o["BRepBndLib"].Add_s(shape, box)
    except Exception:
        pass
    try:
        lo, hi = box.CornerMin(), box.CornerMax()
        return [round(v, 2) for v in
                (lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z())]
    except Exception:
        return None


def _fix_solid(o, solid):
    """ShapeFix an input solid; return (shape, was_valid_before, is_valid_after)."""
    ok0 = o["BRepCheck_Analyzer"](solid).IsValid()
    if ok0:
        return solid, True, True
    fixer = o["ShapeFix_Shape"](solid)
    fixer.Perform()
    fixed = fixer.Shape()
    parts = _solids_of(o, fixed)
    out = parts[0] if parts else fixed
    return out, False, o["BRepCheck_Analyzer"](out).IsValid()


def _read_doc(o, path):
    doc = o["TDocStd_Document"](o["TCollection_ExtendedString"]("in"))
    reader = o["STEPCAFControl_Reader"]()
    reader.SetNameMode(True)
    if reader.ReadFile(path) != o["IFSelect_RetDone"]:
        raise ValueError(f"cannot read STEP file: {path}")
    reader.Transfer(doc)
    return doc, o["XCAFDoc_DocumentTool"].ShapeTool_s(doc.Main())


def _label_name(label):
    from OCP.TDataStd import TDataStd_Name

    a = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), a):
        return a.Get().ToExtString()
    return ""


def _walk_collect(o, st, path_prefix, label, whitelist, found):
    """Depth-first walk resolving reference chains; collect whitelist hits."""
    hops = 0
    while st.IsReference_s(label):
        ref = _new_label()
        if not st.GetReferredShape_s(label, ref):
            return
        label = ref
        hops += 1
        if hops > 12:
            return
    name = _label_name(label)
    here = path_prefix + "_" + name if path_prefix else name
    for suffix, role in whitelist.items():
        if here == suffix or here.endswith(suffix):
            if role not in found:
                try:
                    sh = st.GetShape_s(label)
                except Exception:
                    sh = None
                if sh is not None and not sh.IsNull():
                    found[role] = _solids_of(o, sh)
            return
    if st.IsAssembly_s(label):
        children = o["Sequence_TDF_Label"]()
        st.GetComponents_s(label, children)
        for i in range(1, children.Length() + 1):
            _walk_collect(o, st, here, children.Value(i), whitelist, found)


def _new_label():
    from OCP.TDF import TDF_Label

    return TDF_Label()


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _no_ocp():
    return {
        "status": "error",
        "message": (
            "cadquery-ocp is not installed in this Python "
            "environment. Install with: pip install cadquery-ocp"
        ),
    }


def _handle_stp_recon(args, client):
    if not _HAS_OCP:
        return _no_ocp()
    src = args.get("source_stp", "")
    try:
        src = validate_file_path(src)
    except Exception as e:
        return {"status": "error", "message": f"invalid source_stp: {e}"}
    o = _ocp()
    _, st = _read_doc(o, src)
    seq = o["Sequence_TDF_Label"]()
    st.GetShapes(seq)
    max_lines = int(args.get("max_lines", 200))
    rows = []
    seen_geo = set()  # (bbox, volume): unnamed STEP lists instances AND definitions
    for i in range(1, seq.Length() + 1):
        lab = seq.Value(i)
        name = _label_name(lab)
        try:
            sh = st.GetShape_s(lab)
        except Exception:
            sh = None
        if sh is None or sh.IsNull():
            rows.append({"product": name, "solids": 0})
            continue
        parts = _solids_of(o, sh)
        for j, s in enumerate(parts, 1):
            bb = _bbox(o, s)
            vol = round(_volume(o, s))
            key = (tuple(bb) if bb else None, vol)
            if key in seen_geo:
                continue
            seen_geo.add(key)
            rows.append(
                {
                    "product": name if len(parts) == 1 else f"{name}#{j}",
                    "solids": 1,
                    "volume_mm3": vol,
                    "bbox": bb,
                }
            )
        if len(rows) >= max_lines:
            break
    return {"status": "ok", "source": src, "products": seq.Length(), "rows": rows[:max_lines]}


def _handle_stp_simplify(args, client):
    if not _HAS_OCP:
        return _no_ocp()
    src = args.get("source_stp", "")
    dst = args.get("output_stp", "")
    whitelist = args.get("whitelist") or {}
    fuse_roles = args.get("fuse_roles") or []
    cache_dir = args.get("cache_dir")
    fuzzy = float(args.get("fuzzy", 1e-4))
    try:
        src = validate_file_path(src)
    except Exception as e:
        return {"status": "error", "message": f"invalid source_stp: {e}"}
    try:
        dst = validate_file_path(dst)
    except Exception as e:
        return {"status": "error", "message": f"invalid output_stp: {e}"}
    if not whitelist:
        return {"status": "error", "message": "whitelist must map role -> product-path suffix"}
    o = _ocp()

    # ---- extract whitelist solids (with brep cache) ----
    cache_key = os.path.join(cache_dir, "roles.brep.json") if cache_dir else None

    def cache_path(role):
        return os.path.join(cache_dir, f"{role}.brep")

    roles_solids = {}
    if cache_dir and cache_key and os.path.exists(cache_key):
        builder = o["BRep_Builder"]()
        with open(cache_key, "r", encoding="utf-8") as fh:
            plan = json.load(fh)
        for role in plan["roles"]:
            shape = o["TopoDS_Shape"]()
            o["BRepTools"].Read_s(shape, cache_path(role), builder)
            roles_solids[role] = _solids_of(o, shape)
    else:
        _, st = _read_doc(o, src)
        found = {}
        free = o["Sequence_TDF_Label"]()
        st.GetFreeShapes(free)
        for i in range(1, free.Length() + 1):
            _walk_collect(o, st, "", free.Value(i), whitelist, found)
        missing = [r for r in whitelist if r not in found]
        if missing:
            return {
                "status": "error",
                "message": f"whitelist roles not found in source: {missing}",
                "found": {k: len(v) for k, v in found.items()},
            }
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            builder = o["BRep_Builder"]()
            for role, solids in found.items():
                comp = o["TopoDS_Compound"]()
                builder.MakeCompound(comp)
                for s in solids:
                    builder.Add(comp, s)
                o["BRepTools"].Write_s(comp, cache_path(role))
            with open(cache_key, "w", encoding="utf-8") as fh:
                json.dump({"roles": list(found)}, fh)
        roles_solids = found

    # ---- fix + fuse ----
    report = {"roles": {}, "fused": {}}
    fixed_roles = {}
    for role, solids in roles_solids.items():
        fixed = []
        invalid_fixed = 0
        for s in solids:
            fs, ok0, ok1 = _fix_solid(o, s)
            if not ok0 and not ok1:
                invalid_fixed += 1
            fixed.append(fs)
        fixed_roles[role] = fixed
        report["roles"][role] = {
            "solids": len(fixed),
            "invalid_unfixable": invalid_fixed,
            "volume_mm3": round(sum(_volume(o, s) for s in fixed)),
        }

    for role in fuse_roles:
        if role not in fixed_roles:
            return {"status": "error", "message": f"fuse role not in whitelist: {role}"}
        parts = fixed_roles[role]
        acc = parts[0]
        for i, s in enumerate(parts[1:], 2):
            fuse = o["BRepAlgoAPI_Fuse"](acc, s)
            fuse.SetFuzzyValue(fuzzy)
            fuse.Build()
            if not fuse.IsDone():
                return {
                    "status": "error",
                    "message": f"fuse failed at part {i} of role {role}",
                }
            acc = fuse.Shape()
        # single-solid check
        n = len(_solids_of(o, acc))
        valid = o["BRepCheck_Analyzer"](acc).IsValid()
        if n != 1 or not valid:
            return {
                "status": "error",
                "message": (
                    f"fused {role} is not a single valid solid "
                    f"(nSolids={n}, valid={valid}); "
                    "inspect with cst_stp_recon on the source"
                ),
            }
        fixed_roles[role] = [acc]
        report["fused"][role] = {
            "parts": len(parts),
            "valid": True,
            "volume_mm3": round(_volume(o, acc)),
            "bbox": _bbox(o, acc),
        }

    # ---- export ----
    out_doc = o["TDocStd_Document"](o["TCollection_ExtendedString"]("out"))
    ost = o["XCAFDoc_DocumentTool"].ShapeTool_s(out_doc.Main())
    products = []
    for role, solids in fixed_roles.items():
        if len(solids) == 1:
            comp = solids[0]
        else:
            comp = o["TopoDS_Compound"]()
            builder = o["BRep_Builder"]()
            builder.MakeCompound(comp)
            for s in solids:
                builder.Add(comp, s)
        lab = ost.AddShape(comp, False)
        o["TDataStd_Name"].Set_s(lab, o["TCollection_ExtendedString"](role))
        products.append(role)

    o["Interface_Static"].SetCVal_s("write.step.schema", "AP214")
    writer = o["STEPCAFControl_Writer"]()
    writer.SetNameMode(True)
    if not writer.Transfer(out_doc) or writer.Write(dst) != o["IFSelect_RetDone"]:
        return {"status": "error", "message": f"failed to write {dst}"}
    report["export"] = {
        "path": dst,
        "size_bytes": os.path.getsize(dst),
        "products": products,
    }
    return {"status": "ok", **report}


async def handle(tool_name: str, args: dict, client: "CSTClient") -> list[TextContent]:
    """Handle cad tool calls (pure file processing; no CST connection needed)."""
    try:
        if tool_name == "cst_stp_recon":
            result = _handle_stp_recon(args, client)
        elif tool_name == "cst_stp_simplify":
            result = _handle_stp_simplify(args, client)
        else:
            result = {"status": "error", "message": f"Unknown cad tool: {tool_name}"}
        return _text(result)
    except Exception as e:  # noqa: BLE001 - contract: never raise from handle()
        return _text({"status": "error", "message": str(e)})


def _text(data: dict) -> list[TextContent]:
    """Wrap a dict as a single JSON TextContent response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def register_cad_tools(server, client) -> None:
    """Register STP-side (OCC) simplification tools with the MCP server."""
    from mcp_cst_studio.tools import _registry

    # cad tools are pure file processing: the client is not required,
    # pass None so handlers never touch a CST session.
    _registry.add_module(TOOLS, handle, None)
