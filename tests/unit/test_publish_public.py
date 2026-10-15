# tests/unit/test_publish_public.py — the public-mirror gates must fail closed.
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import publish_public as pp  # noqa: E402

EXCLUDES = ["tasks/", ".cursor/plans/", "audit_*.md", "docs/private_*.md"]


class TestIsExcluded:
    @pytest.mark.parametrize("path", [
        "tasks/lessons.md",
        "tasks/research/agent1_independent_rederivation.md",
        ".cursor/plans/stress_test.md",
        "audit_20260820.md",
        "docs/audit_old.md",          # bare-name glob matches at any depth
        "docs/private_notes.md",      # path glob
    ])
    def test_excluded(self, path):
        assert pp.is_excluded(path, EXCLUDES)

    @pytest.mark.parametrize("path", [
        "README.md",
        "STRATEGY.md",
        "docs/tasks/readme.md",       # 'tasks/' is anchored at the repo root
        ".cursor/rules/style.md",
        "docs/reports/private_notes.md",
        "auditor.py",
    ])
    def test_kept(self, path):
        assert not pp.is_excluded(path, EXCLUDES)


class TestDenylist:
    def test_missing_denylist_refuses(self):
        with pytest.raises(pp.GateFailure, match="missing or empty"):
            pp.compile_denylist([])

    def test_load_patterns_skips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "deny"
        f.write_text("# comment\n\nsecret-id\n  other  \n", encoding="utf-8")
        assert pp.load_patterns(f) == ["secret-id", "other"]
        assert pp.load_patterns(tmp_path / "absent") == []

    def test_hits_plain_and_case_insensitive(self, tmp_path):
        (tmp_path / "a.py").write_text("OWNER = 'Owner@Example.com'\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("clean\n", encoding="utf-8")
        deny = pp.compile_denylist([r"owner@example\.com"])
        hits = pp.denylist_hits(tmp_path, deny, ["a.py", "b.py"])
        assert len(hits) == 1 and hits[0].startswith("a.py")

    def test_hits_inside_docx(self, tmp_path):
        doc = tmp_path / "manual.docx"
        with zipfile.ZipFile(doc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", "<w:t>contact owner@example.com</w:t>")
        deny = pp.compile_denylist([r"owner@example\.com"])
        assert pp.denylist_hits(tmp_path, deny, ["manual.docx"])


class TestSyncMessage:
    def test_first_sync_has_trailer(self):
        msg = pp.sync_message("a" * 40, previous=None)
        assert msg.startswith("Sync with private master @aaaaaaa")
        assert f"{pp.SOURCE_TRAILER}: {'a' * 40}" in msg
