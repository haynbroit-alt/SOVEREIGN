"""Façade applicative : noyau + cerveau + automatisations + commandes.

    app = Sovereign.open()          # ~/.sovereign (ou SOVEREIGN_HOME)
    app.commands.run("/director Que faire cette semaine ?")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sovereign.automation.cycles import CycleRunner
from sovereign.automation.notify import Notifier
from sovereign.automation.queue import TaskQueue
from sovereign.automation.rules import RuleEngine
from sovereign.automation.scheduler import Scheduler
from sovereign.automation.worker import Worker
from sovereign.brain.council import Council
from sovereign.brain.director import Director
from sovereign.brain.governance import ApprovalQueue
from sovereign.brain.world_model import WorldModel
from sovereign.core.clock import Clock
from sovereign.kernel import Kernel


class Sovereign:
    def __init__(self, kernel: Kernel, *, install_rules: bool = True) -> None:
        self.kernel = kernel
        self.queue = TaskQueue(kernel.db, kernel.clock, kernel.settings.automation.task_lease_seconds)
        self.world = WorldModel(kernel.db, kernel.clock)
        self.approvals = ApprovalQueue(self)
        self.director = Director(self)
        self.notifier = Notifier(kernel.settings.automation)
        self.cycles = CycleRunner(self)
        self.scheduler = Scheduler(self)
        self.worker = Worker(self)
        self.rules = RuleEngine(self)
        if install_rules:
            self.rules.install_defaults()
        from sovereign.commands.router import CommandRouter

        self.commands = CommandRouter(self)

    @classmethod
    def open(cls, home: str | Path | None = None, **kwargs: Any) -> Sovereign:
        return cls(Kernel.open(home, **kwargs))

    @classmethod
    def ephemeral(cls, clock: Clock | None = None, **kwargs: Any) -> Sovereign:
        return cls(Kernel.ephemeral(clock=clock, **kwargs))

    def council_for(self, use_llm: bool) -> Council:
        return Council(llm=self.kernel.llm if use_llm and self.kernel.llm.available else None)

    def close(self) -> None:
        self.kernel.close()
