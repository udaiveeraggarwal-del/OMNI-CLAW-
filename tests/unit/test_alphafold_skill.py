import pytest
from omniagent.skills.alphafold.skill import AlphaFoldSkill

def test_alphafold_skill_manifest():
    skill = AlphaFoldSkill()
    assert skill.skill_id == "alphafold"
    assert skill.version == "1.0.0"
    actions = {a.name for a in skill.get_actions()}
    assert "fetch_structure" in actions
    assert "analyze_confidence" in actions

def test_alphafold_fetch_structure_missing_args():
    skill = AlphaFoldSkill()
    result = skill.execute("alphafold.fetch_structure")
    assert "error" in result
    assert "uniprot_id is required" in result["error"]
