"""
Tests de Fase 6 — brex_validator + ste_checker MCP servers.

Cubre:
  - brex_validator: check_wellformed, validate_against_brex,
                    list_brex_rules, extract_s1000d_metadata
  - ste_checker:    check_ste_compliance, list_approved_vocabulary,
                    suggest_corrections
  - Integración: arranque real vía MCPClientManager
"""

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# XML / BREX fixtures
# ---------------------------------------------------------------------------

_VALID_DMODULE = """\
<?xml version="1.0" encoding="UTF-8"?>
<dmodule>
  <identAndStatusSection>
    <dmAddress>
      <dmIdent>
        <dmCode modelIdentCode="ATEST" systemDiffCode="A" systemCode="32"
                subSystemCode="0" subSubSystemCode="0" assyCode="00"
                disassyCode="00" disassyCodeVariant="A" infoCode="040"
                infoCodeVariant="A" itemLocationCode="D"/>
        <issueInfo issueNumber="001" inWork="00"/>
        <language languageIsoCode="en" countryIsoCode="US"/>
      </dmIdent>
    </dmAddress>
    <dmStatus>
      <security securityClassification="01"/>
    </dmStatus>
  </identAndStatusSection>
  <content>
    <description>
      <techName>Hydraulic Power System</techName>
      <infoName>Description</infoName>
    </description>
  </content>
</dmodule>"""

_DMODULE_WITH_FORBIDDEN = """\
<?xml version="1.0" encoding="UTF-8"?>
<dmodule>
  <identAndStatusSection>
    <dmAddress>
      <dmIdent>
        <dmCode modelIdentCode="ATEST" systemCode="32"/>
      </dmIdent>
    </dmAddress>
    <forbidden_element>This element is not allowed</forbidden_element>
  </identAndStatusSection>
</dmodule>"""

_DMODULE_EMPTY_MODELIDENTCODE = """\
<?xml version="1.0" encoding="UTF-8"?>
<dmodule>
  <dmCode modelIdentCode=""/>
</dmodule>"""

_BREX_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<brex>
  <commonInfo>
    <brexTitle>ATEST BREX v1.0</brexTitle>
  </commonInfo>
  <contextRules>
    <structureObjectRule>
      <objectPath allowedObjectFlag="0">//forbidden_element</objectPath>
      <objectUse>forbidden_element is not allowed in ATEST data</objectUse>
    </structureObjectRule>
    <structureObjectRule>
      <objectPath allowedObjectFlag="0">//@modelIdentCode[.='']</objectPath>
      <objectUse>modelIdentCode must not be empty</objectUse>
    </structureObjectRule>
    <structureObjectRule>
      <objectPath allowedObjectFlag="1">//dmCode</objectPath>
      <objectUse>Every dmodule must have a dmCode element</objectUse>
    </structureObjectRule>
  </contextRules>
</brex>"""

_MALFORMED_XML = "<root><unclosed>"


@pytest.fixture
def xml_dir(tmp_path):
    """Write all XML fixtures to tmp_path and return the directory."""
    (tmp_path / "valid_dm.xml").write_text(_VALID_DMODULE, encoding="utf-8")
    (tmp_path / "forbidden_dm.xml").write_text(_DMODULE_WITH_FORBIDDEN, encoding="utf-8")
    (tmp_path / "empty_code_dm.xml").write_text(_DMODULE_EMPTY_MODELIDENTCODE, encoding="utf-8")
    (tmp_path / "brex.xml").write_text(_BREX_XML, encoding="utf-8")
    (tmp_path / "malformed.xml").write_text(_MALFORMED_XML, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# brex_validator — check_wellformed
# ---------------------------------------------------------------------------

class TestCheckWellformed:
    def test_valid_xml_is_wellformed(self, xml_dir):
        from mcp_servers.brex_validator.server import check_wellformed
        result = json.loads(check_wellformed(str(xml_dir / "valid_dm.xml")))
        assert result["wellformed"] is True
        assert result["error"] is None

    def test_malformed_xml_not_wellformed(self, xml_dir):
        from mcp_servers.brex_validator.server import check_wellformed
        result = json.loads(check_wellformed(str(xml_dir / "malformed.xml")))
        assert result["wellformed"] is False
        assert result["error"] is not None

    def test_nonexistent_file_returns_error(self):
        from mcp_servers.brex_validator.server import check_wellformed
        result = json.loads(check_wellformed("/nonexistent/path/file.xml"))
        assert result["wellformed"] is False
        assert "not found" in result["error"].lower()

    def test_brex_itself_is_wellformed(self, xml_dir):
        from mcp_servers.brex_validator.server import check_wellformed
        result = json.loads(check_wellformed(str(xml_dir / "brex.xml")))
        assert result["wellformed"] is True


# ---------------------------------------------------------------------------
# brex_validator — validate_against_brex
# ---------------------------------------------------------------------------

class TestValidateAgainstBrex:
    def test_valid_dm_has_no_violations(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            str(xml_dir / "valid_dm.xml"),
            str(xml_dir / "brex.xml"),
        ))
        assert result["valid"] is True
        assert result["violations"] == []
        assert result["rules_checked"] >= 1

    def test_forbidden_element_detected(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            str(xml_dir / "forbidden_dm.xml"),
            str(xml_dir / "brex.xml"),
        ))
        assert result["valid"] is False
        flags = [v["flag"] for v in result["violations"] if "flag" in v]
        assert "prohibited" in flags

    def test_empty_modelidentcode_detected(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            str(xml_dir / "empty_code_dm.xml"),
            str(xml_dir / "brex.xml"),
        ))
        assert result["valid"] is False
        flags = [v["flag"] for v in result["violations"] if "flag" in v]
        assert "prohibited" in flags

    def test_required_rule_detects_missing_element(self, tmp_path):
        from mcp_servers.brex_validator.server import validate_against_brex
        # XML without dmCode + BREX requiring it
        no_dmcode = '<?xml version="1.0"?><dmodule><other/></dmodule>'
        brex_req = """\
<?xml version="1.0"?><brex><contextRules>
  <structureObjectRule>
    <objectPath allowedObjectFlag="1">//dmCode</objectPath>
    <objectUse>dmCode is required</objectUse>
  </structureObjectRule>
</contextRules></brex>"""
        (tmp_path / "no_dmcode.xml").write_text(no_dmcode, encoding="utf-8")
        (tmp_path / "req_brex.xml").write_text(brex_req, encoding="utf-8")
        result = json.loads(validate_against_brex(
            str(tmp_path / "no_dmcode.xml"),
            str(tmp_path / "req_brex.xml"),
        ))
        assert result["valid"] is False
        flags = [v["flag"] for v in result["violations"] if "flag" in v]
        assert "required" in flags

    def test_nonexistent_xml_returns_error(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            "/nonexistent.xml",
            str(xml_dir / "brex.xml"),
        ))
        assert result["valid"] is False
        assert "error" in result

    def test_nonexistent_brex_returns_error(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            str(xml_dir / "valid_dm.xml"),
            "/nonexistent_brex.xml",
        ))
        assert result["valid"] is False
        assert "error" in result

    def test_malformed_xml_returns_error(self, xml_dir):
        from mcp_servers.brex_validator.server import validate_against_brex
        result = json.loads(validate_against_brex(
            str(xml_dir / "malformed.xml"),
            str(xml_dir / "brex.xml"),
        ))
        assert result["valid"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# brex_validator — list_brex_rules
# ---------------------------------------------------------------------------

class TestListBrexRules:
    def test_returns_all_rules(self, xml_dir):
        from mcp_servers.brex_validator.server import list_brex_rules
        result = json.loads(list_brex_rules(str(xml_dir / "brex.xml")))
        assert result["count"] == 3
        xpaths = [r["xpath"] for r in result["rules"]]
        assert "//forbidden_element" in xpaths

    def test_rule_has_flag_and_description(self, xml_dir):
        from mcp_servers.brex_validator.server import list_brex_rules
        result = json.loads(list_brex_rules(str(xml_dir / "brex.xml")))
        for rule in result["rules"]:
            assert "flag" in rule
            assert "xpath" in rule
            assert "description" in rule

    def test_nonexistent_file_returns_error(self):
        from mcp_servers.brex_validator.server import list_brex_rules
        result = json.loads(list_brex_rules("/nonexistent.xml"))
        assert "error" in result
        assert result["count"] == 0

    def test_empty_brex(self, tmp_path):
        from mcp_servers.brex_validator.server import list_brex_rules
        empty_brex = '<?xml version="1.0"?><brex><contextRules/></brex>'
        p = tmp_path / "empty.xml"
        p.write_text(empty_brex, encoding="utf-8")
        result = json.loads(list_brex_rules(str(p)))
        assert result["count"] == 0
        assert result["rules"] == []


# ---------------------------------------------------------------------------
# brex_validator — extract_s1000d_metadata
# ---------------------------------------------------------------------------

class TestExtractS1000dMetadata:
    def test_extracts_all_fields(self, xml_dir):
        from mcp_servers.brex_validator.server import extract_s1000d_metadata
        result = json.loads(extract_s1000d_metadata(str(xml_dir / "valid_dm.xml")))
        assert "dmCode" in result
        assert result["dmCode"]["modelIdentCode"] == "ATEST"
        assert result["dmCode"]["systemCode"] == "32"
        assert result["issueInfo"]["issueNumber"] == "001"
        assert result["language"]["languageIsoCode"] == "en"
        assert result["security"]["securityClassification"] == "01"
        assert result["techName"] == "Hydraulic Power System"

    def test_partial_metadata_no_error(self, tmp_path):
        from mcp_servers.brex_validator.server import extract_s1000d_metadata
        minimal = '<?xml version="1.0"?><dmodule><dmCode modelIdentCode="X"/></dmodule>'
        p = tmp_path / "minimal.xml"
        p.write_text(minimal, encoding="utf-8")
        result = json.loads(extract_s1000d_metadata(str(p)))
        assert result["dmCode"]["modelIdentCode"] == "X"
        assert "issueInfo" not in result

    def test_nonexistent_returns_error(self):
        from mcp_servers.brex_validator.server import extract_s1000d_metadata
        result = json.loads(extract_s1000d_metadata("/no.xml"))
        assert "error" in result


# ---------------------------------------------------------------------------
# ste_checker — check_ste_compliance
# ---------------------------------------------------------------------------

class TestCheckSteCompliance:
    def test_clean_text_no_violations(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        text = "Remove the bolt. Clean the surface. Install the new seal."
        result = json.loads(check_ste_compliance(text))
        assert result["violations"] == []
        assert result["overall_score"] == 1.0

    def test_long_sentence_detected(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        long_sent = ("The hydraulic system assembly supplies pressurized fluid "
                     "to the landing gear actuator through the main pressure line "
                     "and control valve unit.")
        result = json.loads(check_ste_compliance(long_sent))
        types = [v["type"] for v in result["violations"]]
        assert "long_sentence" in types

    def test_passive_voice_detected(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance(
            "The bolt is tightened to the correct torque value."
        ))
        types = [v["type"] for v in result["violations"]]
        assert "passive_voice" in types

    def test_unapproved_word_utilize(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance(
            "Utilize the torque wrench to tighten the bolt."
        ))
        types = [v["type"] for v in result["violations"]]
        assert "unapproved_word" in types
        words = [v.get("word") for v in result["violations"] if v["type"] == "unapproved_word"]
        assert "utilize" in words

    def test_unapproved_word_ensure(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance(
            "Ensure the valve is in the open position."
        ))
        words = [v.get("word") for v in result["violations"] if v["type"] == "unapproved_word"]
        assert "ensure" in words

    def test_multiple_violations_in_same_text(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        text = ("Utilize the tool to ensure the component is correctly installed "
                "and the pressure value is verified.")
        result = json.loads(check_ste_compliance(text))
        assert result["stats"]["violations_count"] >= 2

    def test_empty_text(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance(""))
        assert result["violations"] == []
        assert result["stats"]["sentence_count"] == 0

    def test_strict_vocabulary_flags_unknown_words(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance(
            "Recalibrate the instrument.",
            strict_vocabulary=True,
        ))
        types = [v["type"] for v in result["violations"]]
        assert "unknown_word" in types

    def test_stats_sentence_count(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance("Remove the bolt. Install the seal."))
        assert result["stats"]["sentence_count"] == 2

    def test_score_decreases_with_violations(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        clean = json.loads(check_ste_compliance("Remove the bolt."))
        messy = json.loads(check_ste_compliance(
            "Utilize the tool to ensure the component is approximately correct."
        ))
        assert messy["overall_score"] < clean["overall_score"]

    def test_case_insensitive_detection(self):
        from mcp_servers.ste_checker.server import check_ste_compliance
        result = json.loads(check_ste_compliance("UTILIZE the wrench."))
        words = [v.get("word") for v in result["violations"] if v["type"] == "unapproved_word"]
        assert "utilize" in words


# ---------------------------------------------------------------------------
# ste_checker — list_approved_vocabulary
# ---------------------------------------------------------------------------

class TestListApprovedVocabulary:
    def test_all_returns_large_set(self):
        from mcp_servers.ste_checker.server import list_approved_vocabulary
        result = json.loads(list_approved_vocabulary())
        assert result["count"] > 100
        assert result["category"] == "all"
        assert "use" in result["words"]

    def test_verbs_category(self):
        from mcp_servers.ste_checker.server import list_approved_vocabulary
        result = json.loads(list_approved_vocabulary("verbs"))
        assert result["category"] == "verbs"
        assert "remove" in result["words"]

    def test_nouns_category(self):
        from mcp_servers.ste_checker.server import list_approved_vocabulary
        result = json.loads(list_approved_vocabulary("nouns"))
        assert "bolt" in result["words"]
        assert "seal" in result["words"]

    def test_invalid_category_returns_all(self):
        from mcp_servers.ste_checker.server import list_approved_vocabulary
        result = json.loads(list_approved_vocabulary("bogus_category"))
        assert result["category"] == "all"
        assert result["count"] > 0

    def test_words_are_sorted(self):
        from mcp_servers.ste_checker.server import list_approved_vocabulary
        result = json.loads(list_approved_vocabulary("nouns"))
        words = result["words"]
        assert words == sorted(words)


# ---------------------------------------------------------------------------
# ste_checker — suggest_corrections
# ---------------------------------------------------------------------------

class TestSuggestCorrections:
    def test_replaces_utilize_with_use(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        result = json.loads(suggest_corrections("Utilize the torque wrench."))
        assert "use" in result["corrected_text"].lower()
        assert any(c["replaced"] == "utilize" for c in result["changes"])

    def test_preserves_leading_capital(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        result = json.loads(suggest_corrections("Utilize the wrench."))
        # "Utilize" → "Use" (capitalized)
        assert result["corrected_text"].startswith("Use")

    def test_multiple_replacements(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        result = json.loads(suggest_corrections(
            "Utilize the tool and ensure the seal is correct."
        ))
        replaced_words = [c["replaced"] for c in result["changes"]]
        assert "utilize" in replaced_words
        assert "ensure" in replaced_words

    def test_no_changes_for_clean_text(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        result = json.loads(suggest_corrections("Remove the bolt. Install the seal."))
        assert result["changes"] == []

    def test_long_sentence_recommendation(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        long = ("The hydraulic system assembly supplies pressurized fluid "
                "to the landing gear actuator through the main pressure line "
                "and control valve unit.")
        result = json.loads(suggest_corrections(long))
        assert any("long sentence" in r.lower() for r in result["recommendations"])

    def test_passive_voice_recommendation(self):
        from mcp_servers.ste_checker.server import suggest_corrections
        result = json.loads(suggest_corrections(
            "The bolt is tightened to the correct torque value."
        ))
        assert any("passive" in r.lower() for r in result["recommendations"])


# ---------------------------------------------------------------------------
# Integración: servidores arrancan vía MCPClientManager
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_brex_validator_server_starts_and_lists_tools():
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    config = MCPServerConfig(
        name="brex_validator_test",
        type="stdio",
        command="python",
        args=["-m", "mcp_servers.brex_validator.server"],
        enabled=True,
        timeout_seconds=30,
    )
    try:
        ok = await manager.start_server(config)
        assert ok, "brex_validator server failed to start"
        tools = await manager.list_tools("brex_validator_test")
        tool_names = {t.name for t in tools}
        assert "check_wellformed" in tool_names
        assert "validate_against_brex" in tool_names
        assert "list_brex_rules" in tool_names
        assert "extract_s1000d_metadata" in tool_names
    finally:
        await manager.stop_all()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ste_checker_server_starts_and_lists_tools():
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    config = MCPServerConfig(
        name="ste_checker_test",
        type="stdio",
        command="python",
        args=["-m", "mcp_servers.ste_checker.server"],
        enabled=True,
        timeout_seconds=30,
    )
    try:
        ok = await manager.start_server(config)
        assert ok, "ste_checker server failed to start"
        tools = await manager.list_tools("ste_checker_test")
        tool_names = {t.name for t in tools}
        assert "check_ste_compliance" in tool_names
        assert "list_approved_vocabulary" in tool_names
        assert "suggest_corrections" in tool_names
    finally:
        await manager.stop_all()
