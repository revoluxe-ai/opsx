from loop_engine import BugbotRepairLoop


class RepoPathAdapter:
    repo_path = "C:/Users/vkita/my_code/game"

    def session_id(self) -> str:
        return "session-0"

    def reset_session(self, *, reason: str | None = None) -> str:
        return self.session_id()

    def execute_prompt(self, prompt: str, *, step: str | None = None):
        raise AssertionError("not called in this test")


def test_loop_uses_adapter_repo_path_for_preflight(monkeypatch):
  called: dict[str, str] = {}

  def fake_collect(repo_path: str, *, base_ref: str = "stage", path_prefixes=None):
      called["repo_path"] = repo_path
      from git_preflight import GitPreflightResult

      return GitPreflightResult(ok=False, base_ref=base_ref, error="stop test")

  monkeypatch.setattr("loop_engine.collect_git_preflight", fake_collect)
  result = BugbotRepairLoop(RepoPathAdapter())._resolve_review_context()
  assert called["repo_path"] == RepoPathAdapter.repo_path
  assert result[2] == "stop test"
