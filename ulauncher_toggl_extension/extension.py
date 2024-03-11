import logging
from functools import partial
from pathlib import Path
from typing import Callable, Iterable

from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction
from ulauncher.api.shared.event import (
    ItemEnterEvent,
    KeywordQueryEvent,
    PreferencesEvent,
    PreferencesUpdateEvent,
)
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.item.ExtensionSmallResultItem import ExtensionSmallResultItem
from ulauncher.utils.fuzzy_search import get_score

from ulauncher_toggl_extension.toggl.cli import (
    TogglProjects,
    TrackerCli,
)
from ulauncher_toggl_extension.toggl.manager import TogglViewer, QueryParameters

from .preferences import (
    PreferencesEventListener,
    PreferencesUpdateEventListener,
)

log = logging.getLogger(__name__)


class TogglExtension(Extension):
    __slots__ = (
        "_toggl_exec_path",
        "_max_results",
        "_toggl_workspace",
        "_toggl_hints",
    )

    def __init__(self) -> None:
        super().__init__()
        self.subscribe(
            KeywordQueryEvent,
            KeywordQueryEventListener(),
        )
        self.subscribe(
            ItemEnterEvent,
            ItemEnterEventListener(),
        )
        self.subscribe(
            PreferencesUpdateEvent,
            PreferencesUpdateEventListener(),
        )
        self.subscribe(
            PreferencesEvent,
            PreferencesEventListener(),
        )

        self._toggl_exec_path = Path.home() / Path(".local/bin/toggl")
        self._max_results = 10
        self._toggl_hints = True
        self._toggl_workspace = None

        # OPTIMIZE: Possibly turn these cache functiosn into async methods.
        tcli = TrackerCli(
            self._toggl_exec_path,
            self._max_results,
            self._toggl_workspace,
        )
        log.debug("Updating trackers")
        tcli.list_trackers()

        pcli = TogglProjects(
            self._toggl_exec_path,
            self._max_results,
            self._toggl_workspace,
        )
        log.debug("Updating projects")
        pcli.list_projects()

    def process_query(self, query: list[str]) -> list | Callable:
        tviewer = TogglViewer(self)

        check = tviewer.pre_check_cli()
        if isinstance(check, list):
            return check

        if len(query) == 1:
            defaults = tviewer.default_options(*query)
            return self.generate_results(defaults)

        query.pop(0)

        QUERY_MATCH = {
            "start": tviewer.start_tracker,
            "add": tviewer.add_tracker,
            "continue": tviewer.continue_tracker,
            "stop": tviewer.stop_tracker,
            "edit": tviewer.edit_tracker,
            "now": tviewer.edit_tracker,
            "delete": tviewer.remove_tracker,
            "remove": tviewer.remove_tracker,
            "report": tviewer.total_trackers,
            "sum": tviewer.total_trackers,
            "list": tviewer.list_trackers,
            "project": tviewer.get_projects,
            "help": partial(
                tviewer.generate_basic_hints,
                max_values=self.max_results,
                default_action=SetUserQueryAction("tgl "),
            ),
        }

        method = QUERY_MATCH.get(
            query[0],
            partial(
                self.create_results,
                QUERY_MATCH,
            ),
        )

        q = query.pop(0)
        kwargs = self.parse_query(query)

        results = method(*query, query=q, **kwargs)  # type: ignore[operator]
        if not results:
            defaults = tviewer.default_options(*query)
            return self.generate_results(defaults)

        if query and query[-1] == "@":
            results = [results[0]]
            q = ["tgl", q]
            q.extend(query)
            results.extend(
                tviewer.manager.list_projects(
                    query=q,
                    post_method=tviewer.manager.query_builder,
                    **kwargs,
                )
            )

        return self.generate_results(results)

    def match_query(
        self,
        query: str,
        target: str,
        threshold: int = 50,
    ) -> bool:
        return get_score(query, target) >= threshold

    def create_results(
        self,
        match_dict: dict,
        query: str,
    ) -> list[QueryParameters]:
        results = []
        matched_results = set()
        for trg, fn in match_dict.items():
            if self.match_query(query, trg) and fn not in matched_results:
                try:
                    results.append(fn()[0])
                except TypeError:
                    continue
                matched_results.add(fn)

        return results

    def parse_query(self, query: list[str]) -> dict[str, str]:
        # TODO: Input sanitizing in order to throw away invalid arguments and prevent erorrs.
        arguments = {}
        for item in query:
            if item[0] == "#":
                arguments["tags"] = item[1:]
            elif item[0] == "@":
                item = item[1:]
                try:
                    item = int(item)  # mypy: ignore [operator]
                except ValueError:
                    pass
                arguments["project"] = item
            elif item[0] == ">" and item[-1] == "<":
                arguments["duration"] = item[1:-1]
            elif item[0] == ">":
                arguments["start"] = item[1:]
            elif item[0] == "<":
                arguments["stop"] = item[1:]

        return arguments

    def generate_results(
        self,
        actions: Iterable[QueryParameters],
    ) -> list[ExtensionResultItem]:
        results = []

        for i, item in enumerate(actions, start=1):
            if item.small:
                action = ExtensionSmallResultItem(
                    icon=str(item.icon),
                    name=f"{item.name}: {item.description}",
                    description=item.description,
                    on_enter=item.on_enter,
                    on_alt_enter=item.on_alt_enter,
                    highlightable=False,
                )
            else:
                action = ExtensionResultItem(
                    icon=str(item.icon),
                    name=item.name,
                    description=item.description,
                    on_enter=item.on_enter,
                    on_alt_enter=item.on_alt_enter,
                )

            results.append(action)

            if i == self.max_results:
                break

        return results

    @property
    def toggl_exec_path(self) -> Path:
        return self._toggl_exec_path

    @property
    def default_project(self) -> int | None:
        return self._default_project

    @property
    def max_results(self) -> int:
        return self._max_results

    @property
    def toggled_hints(self) -> bool:
        return self._toggled_hints


class KeywordQueryEventListener(EventListener):
    def on_event(
        self,
        event: KeywordQueryEvent,
        extension: TogglExtension,
    ) -> None:
        query = event.get_query().split()
        processed_query = extension.process_query(query)

        return RenderResultListAction(processed_query)


class ItemEnterEventListener(EventListener):
    def on_event(
        self,
        event: ItemEnterEvent,
        extension: TogglExtension,
    ) -> None:
        data = event.get_data()

        execution = data()
        if not isinstance(execution, bool):
            results = extension.generate_results(execution)
            return RenderResultListAction(results)

        if not execution:
            return SetUserQueryAction("tgl ")

        log.info("Successfuly excecuted %s", data)

        return HideWindowAction()


if __name__ == "__main__":
    pass
