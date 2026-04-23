"""
brex_validator MCP server — valida documentos S1000D contra reglas BREX.

Herramientas:
  check_wellformed        — verifica que un XML está bien formado
  validate_against_brex   — evalúa structureObjectRule con XPath (lxml)
  list_brex_rules         — extrae las reglas de un documento BREX
  extract_s1000d_metadata — lee dmCode, issueInfo, language, security de un data module
"""

import json
import pathlib
from typing import Optional

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from lxml import etree as ET

mcp = FastMCP(name="brex_validator")

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_path(path_str: str) -> pathlib.Path:
    """Resolve a path string against known project directories."""
    p = pathlib.Path(path_str)
    if p.exists():
        return p
    # Try relative to project root
    candidate = (_PROJECT_ROOT / path_str).resolve()
    if candidate.exists():
        return candidate
    # Try filename only in demo_data/
    candidate = _PROJECT_ROOT / "demo_data" / p.name
    if candidate.exists():
        return candidate
    # Try filename only in output/
    candidate = _PROJECT_ROOT / "output" / p.name
    if candidate.exists():
        return candidate
    return p  # Return original; caller will handle not-found


def _read_file(path_str: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (content, error)."""
    p = _resolve_path(path_str)
    if not p.exists():
        return None, f"File not found: {path_str}"
    if not p.is_file():
        return None, f"Not a file: {path_str}"
    try:
        return p.read_text(encoding="utf-8", errors="replace"), None
    except Exception as e:
        return None, f"Cannot read file: {e}"


def _parse_xml(content: str) -> tuple[Optional[ET._Element], Optional[str]]:
    """Returns (root_element, error_string)."""
    try:
        root = ET.fromstring(content.encode("utf-8"))
        return root, None
    except ET.XMLSyntaxError as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def check_wellformed(xml_path: str) -> str:
    """
    Verifica si un fichero XML está bien formado (parsing sin errores).

    Returns JSON: {"wellformed": bool, "error": str|null, "path": str}
    """
    content, err = _read_file(xml_path)
    if err:
        return json.dumps({"wellformed": False, "error": err, "path": xml_path})
    _, parse_err = _parse_xml(content)
    return json.dumps({"wellformed": parse_err is None, "error": parse_err, "path": xml_path})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def validate_against_brex(xml_path: str, brex_path: str) -> str:
    """
    Valida un fichero XML S1000D contra las structureObjectRule de un documento BREX.

    Semántica de allowedObjectFlag:
      "0" = prohibido — violación si el XPath encuentra nodos
      "1" = requerido — violación si el XPath no encuentra nodos

    Returns JSON: {"valid": bool, "violations": [...], "rules_checked": int}
    """
    xml_content, err = _read_file(xml_path)
    if err:
        return json.dumps({"valid": False, "violations": [], "error": err, "rules_checked": 0})

    brex_content, err = _read_file(brex_path)
    if err:
        return json.dumps({"valid": False, "violations": [], "error": err, "rules_checked": 0})

    xml_root, err = _parse_xml(xml_content)
    if err:
        return json.dumps({"valid": False, "violations": [], "error": f"XML: {err}", "rules_checked": 0})

    brex_root, err = _parse_xml(brex_content)
    if err:
        return json.dumps({"valid": False, "violations": [], "error": f"BREX: {err}", "rules_checked": 0})

    violations = []
    rules_checked = 0

    for rule in brex_root.iter("structureObjectRule"):
        obj_path_el = rule.find("objectPath")
        obj_use_el = rule.find("objectUse")
        if obj_path_el is None:
            continue

        xpath_expr = (obj_path_el.text or "").strip()
        allowed_flag = obj_path_el.get("allowedObjectFlag", "0")
        description = (obj_use_el.text or "").strip() if obj_use_el is not None else xpath_expr

        try:
            matches = xml_root.xpath(xpath_expr)
            rules_checked += 1
        except (ET.XPathEvalError, ET.XPathSyntaxError) as e:
            violations.append({"rule": description, "xpath": xpath_expr, "error": str(e)})
            continue

        n = len(matches)
        if allowed_flag == "0" and n > 0:
            violations.append({
                "rule": description,
                "xpath": xpath_expr,
                "flag": "prohibited",
                "matches_found": n,
            })
        elif allowed_flag == "1" and n == 0:
            violations.append({
                "rule": description,
                "xpath": xpath_expr,
                "flag": "required",
                "matches_found": 0,
            })

    return json.dumps({
        "valid": len(violations) == 0,
        "violations": violations,
        "rules_checked": rules_checked,
    })


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def list_brex_rules(brex_path: str) -> str:
    """
    Extrae y lista las structureObjectRule de un documento BREX S1000D.

    Returns JSON: {"rules": [...], "count": int}
    """
    content, err = _read_file(brex_path)
    if err:
        return json.dumps({"rules": [], "count": 0, "error": err})

    root, parse_err = _parse_xml(content)
    if parse_err:
        return json.dumps({"rules": [], "count": 0, "error": parse_err})

    rules = []
    for rule in root.iter("structureObjectRule"):
        obj_path_el = rule.find("objectPath")
        obj_use_el = rule.find("objectUse")
        if obj_path_el is None:
            continue
        rules.append({
            "xpath": (obj_path_el.text or "").strip(),
            "flag": obj_path_el.get("allowedObjectFlag", "0"),
            "description": (obj_use_el.text or "").strip() if obj_use_el is not None else "",
        })

    return json.dumps({"rules": rules, "count": len(rules)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def extract_s1000d_metadata(xml_path: str) -> str:
    """
    Extrae los metadatos S1000D de un data module.

    Campos: dmCode, issueInfo, language, security, techName, infoName.
    Returns JSON con los campos encontrados (omite los ausentes).
    """
    content, err = _read_file(xml_path)
    if err:
        return json.dumps({"error": err})

    root, parse_err = _parse_xml(content)
    if parse_err:
        return json.dumps({"error": parse_err})

    def _attrs(tag: str, *names) -> Optional[dict]:
        el = root.find(f".//{tag}")
        if el is None:
            return None
        result = {n: el.get(n) for n in names if el.get(n) is not None}
        return result or None

    def _text(tag: str) -> Optional[str]:
        el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else None

    meta: dict = {}

    dm_code = _attrs(
        "dmCode",
        "modelIdentCode", "systemDiffCode", "systemCode",
        "subSystemCode", "subSubSystemCode", "assyCode",
        "disassyCode", "disassyCodeVariant", "infoCode",
        "infoCodeVariant", "itemLocationCode",
    )
    if dm_code:
        meta["dmCode"] = dm_code

    issue_info = _attrs("issueInfo", "issueNumber", "inWork")
    if issue_info:
        meta["issueInfo"] = issue_info

    language = _attrs("language", "languageIsoCode", "countryIsoCode")
    if language:
        meta["language"] = language

    security = _attrs("security", "securityClassification")
    if security:
        meta["security"] = security

    for tag in ("techName", "infoName"):
        val = _text(tag)
        if val:
            meta[tag] = val

    return json.dumps(meta)


if __name__ == "__main__":
    mcp.run()
