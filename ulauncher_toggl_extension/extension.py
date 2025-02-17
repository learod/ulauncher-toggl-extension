from __future__ import annotations

import contextlib
import logging
import re
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
from ulauncher_toggl_extension.toggl.manager import QueryParameters, TogglViewer

from .preferences import (
    PreferencesEventListener,
    PreferencesUpdateEventListener,
)

log = logging.getLogger(__name__)


class TogglExtension(Extension):
    """Main extension clas housing most of querying funtionality.

    Methods:
        process_query: Processes query and returns results to be displayed
            inside the launcher.
        generate_results: Converts results from TogglCli into ULauncher items.

    """

    __slots__ = (
        "_toggl_exec_path",
        "_max_results",
        "_toggl_workspace",
        "_toggl_hints",
        "_default_project",
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
        self._default_project = None

        # OPTIMIZE: Possibly turn these cache functiosn into async methods.
        tcli = TrackerCli(
            self._toggl_exec_path,
            self._max_results,
            self._toggl_workspace,
        )
        log.debug("Updating trackers")
        tcli.fetch_objects()

        pcli = TogglProjects(
            self._toggl_exec_path,
            self._max_results,
            self._toggl_workspace,
        )
        log.debug("Updating projects")
        pcli.fetch_objects()

    def process_query(self, query: list[str], **kwargs) -> list | Callable:
        """Main method that handles querying for functionality.

        Could be refactored to be more readable as needed if more functions are
        being added.

        Args:
            query (list[str]): List of query terms to parse.

        Returns:
            query (list[str]) | Callable: List or oneq uery terms to display.
        """
        # HACK: Some query handling is still a mess as some function have
        # different signatures.

        tviewer = TogglViewer(self)

        check = tviewer.pre_check_cli()
        if isinstance(check, list):
            return check

        if not query:
            defaults = tviewer.default_options(*query)
            return self.generate_results(defaults)

        query_match = {
            "start": tviewer.start_tracker,
            "add": tviewer.add_tracker,
            "continue": tviewer.continue_tracker,
            "cont": tviewer.continue_tracker,
            "cnt": tviewer.continue_tracker,
            "stop": tviewer.stop_tracker,
            "end": tviewer.stop_tracker,
            "edit": tviewer.edit_tracker,
            "now": tviewer.edit_tracker,
            "delete": tviewer.remove_tracker,
            "del": tviewer.remove_tracker,
            "remove": tviewer.remove_tracker,
            "report": tviewer.total_trackers,
            "sum": tviewer.total_trackers,
            "list": tviewer.list_trackers,
            "project": tviewer.list_projects,
            "help": partial(
                tviewer.generate_basic_hints,
                max_values=self.max_results,
                default_action=SetUserQueryAction("tgl "),
            ),
        }

        q = query.pop(0)
        method = query_match.get(
            q,
            partial(
                self.match_results,
                match_dict=query_match,
                **kwargs,
            ),
        )

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
                ),
            )

        return self.generate_results(results)

    @staticmethod
    def match_query(
        query: str,
        target: str,
        threshold: int = 50,
    ) -> bool:
        return get_score(query, target) >= threshold

    def match_results(
        self,
        *args,
        match_dict: dict[str, Callable],
        query: str,
        **kwargs,
    ) -> list[QueryParameters]:
        """Fuzzy matches query terms against a dictionary of functions using
        the `match_query` method.

        Will ignore any other parameters supplied with *_ parameters.

        Args:
            match_dict(dict): Dictionary of functions to match against.
                Will only display the first result of each viwer method.
            query (str): Query term to match against.

        Returns:
            list: List of possible matches that are produced by matched
                functions.
        """
        results = []
        matched_results = set()
        for trg, fn in match_dict.items():
            if fn not in matched_results and TogglExtension.match_query(query, trg):
                try:
                    results.append(fn(*args, **kwargs)[0])
                except TypeError:
                    continue
                matched_results.add(fn)

        return results

    def generate_results(
        self,
        actions: Iterable[QueryParameters],
    ) -> list[ExtensionResultItem]:
        """Generates results from pre defined parameters.

        Args:
            actions: Iterable[QueryParameters]: Items to generate results from.

        Returns:
            list[ExtensionResultItem]: List of results to display in the launcher.
        """
        results = []

        # OPTIMIZE: Process actions here to make testing eaiser in the rest of
        # of the modules.

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

    @toggl_exec_path.setter
    def toggl_exec_path(self, path: Path) -> None:
        self._toggl_exec_path = path

    @property
    def default_project(self) -> int | None:
        return self._default_project

    @default_project.setter
    def default_project(self, project: int | None) -> None:
        self._default_project = project

    @property
    def max_results(self) -> int:
        return self._max_results

    @max_results.setter
    def max_results(self, results: int) -> None:
        self._max_results = results

    @property
    def toggled_hints(self) -> bool:
        return self._toggl_hints

    @toggled_hints.setter
    def toggled_hints(self, hints: bool) -> None:
        self._toggl_hints = hints


class KeywordQueryEventListener(EventListener):
    """Event listener for keyword query events.

    Args:
        parse_query: Parses query into a dictionary of arguments useable by the
            rest of the extension.
    """

    def on_event(
        self,
        event: KeywordQueryEvent,
        extension: TogglExtension,
    ) -> None:
        args = event.get_argument()
        if args:
            query = args.split(" ")
            arguments = self.parse_query(args, query)
            log.debug("Query Action: %s", query[0])
            log.debug("Arguments: %s", arguments)
        else:
            query = []
            arguments = {}

        processed_query = extension.process_query(query, **arguments)

        return RenderResultListAction(processed_query)

    def parse_query(self, query: str, args: list[str]) -> dict[str, str]:  # noqa: C901
        """Parses query into a dictionary of arguments useable by the rest of
        the extension.

        Args:
            query (list[str]): List of query terms to parse.

        Returns:
            dict: Dictionary of query terms and values usea
        """

        # TODO: Input sanitizing in order to throw away invalid arguments.

        arguments = {}
        desc_patt = r'(?P<desc>(?<=")[\w \-]+(?="))'
        # OPTIMIZE: Might be able to find a size fits all regex in the future.
        desc = re.search(desc_patt, query)
        if desc:
            arguments["description"] = desc.group("desc")
        for i, item in enumerate(args[1:]):
            if item[0] == "#":
                arguments["tags"] = item[1:].split(",")
            elif item[0] == "@":
                item = item[1:]
                with contextlib.suppress(ValueError):
                    item = int(item)  # mypy: ignore [operator]
                arguments["project"] = item
            elif item[0] == ">" and item[-1] == "<":
                arguments["duration"] = item[1:-1]
            elif item[0] == ">":
                arguments["start"] = item[1:]
                if i + 1 < len(query) and query[i + 1] in {"AM", "PM"}:
                    arguments["start"] += " " + query[i + 1]
            elif item[0] == "<":
                arguments["stop"] = item[1:]
                if i + 1 < len(query) and query[i + 1] in {"AM", "PM"}:
                    arguments["stop"] += " " + query[i + 1]
            elif item == "refresh":
                arguments["refresh"] = True

        return arguments


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
