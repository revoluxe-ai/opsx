"""Единый журнал багов OPSX: идентичность, события и DI-порт.

Только ``BugJournal`` выдаёт ``bug_id`` и пишет события. Агенты передают
наблюдения, не пути файлов. ``change_id`` задаётся при открытии журнала прогона.
"""

from bug_log.file_journal import FileBugJournal, open_run_journal
from bug_log.ids import BugRef
from bug_log.null_journal import NullBugJournal
from bug_log.port import BugJournal

__all__ = [
    "BugJournal",
    "BugRef",
    "FileBugJournal",
    "NullBugJournal",
    "open_run_journal",
]
