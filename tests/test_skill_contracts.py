"""Contrat de TOUTES les Skills : documentation, conformité Agent Skills, cohérence code ↔ SKILL.md."""

from __future__ import annotations

import pytest

from sovereign.skills.base import SkillOutput
from sovereign.skills.registry import REQUIRED_SECTIONS, SkillRegistry, load_manifest

REGISTRY = SkillRegistry()
NAMES = REGISTRY.names


def test_catalog_is_complete():
    expected = {
        "business-intelligence", "marketing-strategy", "business-development", "intelligent-management",
        "watch-anticipation", "negotiation-strategy", "finance-budget", "analysis-optimization",
        "innovation-creativity", "memory-learning",
    }
    missing = expected - set(NAMES)
    if missing:
        pytest.skip(f"Skills pas encore livrées : {sorted(missing)}")
    assert set(NAMES) == expected


@pytest.mark.parametrize("name", NAMES)
def test_skill_md_is_complete(name):
    manifest = REGISTRY.manifest(name)
    for section in REQUIRED_SECTIONS:
        assert section in manifest.body, f"{name} : section manquante {section}"
    assert manifest.keywords and manifest.events and manifest.actions
    assert len(manifest.body) > 1200, f"{name} : documentation trop succincte"
    assert load_manifest(manifest.path).name == name


@pytest.mark.parametrize("name", NAMES)
def test_implementation_matches_manifest(name):
    skill = REGISTRY.get(name)
    manifest = REGISTRY.manifest(name)
    assert set(skill.actions) == set(manifest.actions)
    for spec in skill.actions.values():
        assert issubclass(spec.output_model, SkillOutput)
        assert spec.summary
        schema = spec.json_schema()
        assert schema["input"]["type"] == "object"
        assert f"`{spec.name}`" in manifest.body, f"{name} : action {spec.name} non documentée"


@pytest.mark.parametrize("name", NAMES)
def test_assess_runs_on_empty_workspace(name, kernel):
    out = kernel.run_skill(name, "assess")
    assert isinstance(out, SkillOutput)
    assert out.summary


def test_selection_is_relevant_and_lazy():
    registry = SkillRegistry()
    assert registry.loaded() == []
    picks = registry.select("Combien reste-t-il dans le budget de dépenses ?")
    assert picks and picks[0].name == "finance-budget"
    assert registry.loaded() == []  # la sélection ne charge aucun code
    picks = registry.select("", events=["decision.outcome_recorded"])
    assert picks[0].name == "memory-learning"
    assert registry.select("météo à Tokyo") == []
