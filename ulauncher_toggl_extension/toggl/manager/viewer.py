from __future__ import annotations

from functools import cache, partial
from typing import TYPE_CHECKING

from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction

from ulauncher_toggl_extension.toggl import TogglTracker
from ulauncher_toggl_extension.toggl.cli import TrackerCli
from ulauncher_toggl_extension.toggl.images import (
    ADD_IMG,
    APP_IMG,
    BROWSER_IMG,
    CONTINUE_IMG,
    DELETE_IMG,
    EDIT_IMG,
    REPORT_IMG,
    START_IMG,
    STOP_IMG,
)

from .manager import QueryParameters, TipSeverity, TogglManager

if TYPE_CHECKING:
    from ulauncher.api.shared.action.BaseAction import BaseAction

    from ulauncher_toggl_extension.extension import TogglExtension


class TogglViewer:
    __slots__ = (
        "toggl_exec_path",
        "max_results",
        "default_project",
        "tcli",
        "manager",
        "extension",
        "hints",
        "current_tracker",
    )

    def __init__(self, ext: TogglExtension) -> None:
        self.toggl_exec_path = ext.toggl_exec_path
        self.max_results = ext.max_results
        self.default_project = ext.default_project
        self.hints = ext.toggled_hints

        self.tcli = TrackerCli(
            self.toggl_exec_path,
            self.max_results,
            self.default_project,
        )
        self.manager = TogglManager(
            self.toggl_exec_path,
            self.max_results,
            self.default_project,
        )

        self.current_tracker = self.tcli.check_running()

    def pre_check_cli(self) -> list | None:
        if not self.toggl_exec_path.exists():
            warning = self.manager.generate_hint(
                "TogglCli is not properly configured.",
                SetUserQueryAction(""),
                TipSeverity.ERROR,
                small=False,
            )
            warning.extend(
                self.manager.generate_hint(
                    "Check your Toggl exectutable path in the config.",
                    DoNothingAction(),
                    TipSeverity.INFO,
                ),
            )
            return warning

        return None

    def default_options(self, *args, **kwargs) -> list[QueryParameters]:
        basic_tasks = [
            QueryParameters(
                START_IMG,
                "Start",
                "Start a Toggl tracker",
                SetUserQueryAction("tgl start"),
            ),
            QueryParameters(
                START_IMG,
                "Add",
                "Add a toggl time tracker.",
                SetUserQueryAction("tgl add"),
            ),
            QueryParameters(
                DELETE_IMG,
                "Delete",
                "Delete a Toggl time tracker",
                SetUserQueryAction("tgl delete"),
            ),
            self.total_trackers()[0],
            self.list_trackers(*args, **kwargs)[0],
            self.get_projects(*args, **kwargs)[0],
        ]
        if self.current_tracker is None:
            current = [
                QueryParameters(
                    CONTINUE_IMG,
                    "Continue",
                    "Continue the latest Toggl time tracker",
                    ExtensionCustomAction(
                        partial(self.manager.continue_tracker),
                    ),
                    SetUserQueryAction("tgl continue"),
                ),
            ]
        else:
            current = [
                QueryParameters(
                    self.current_tracker.find_color_svg(APP_IMG),
                    f"Currently Running: {self.current_tracker.description}",
                    f"Since: {self.current_tracker.start}\
                      @{self.current_tracker.project[0]}",
                    ExtensionCustomAction(
                        partial(
                            self.edit_tracker,
                            current=self.current_tracker,
                        ),
                        keep_app_open=True,
                    ),
                ),
                self.stop_tracker()[0],
            ]

        current.extend(basic_tasks)

        return current

    def continue_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = CONTINUE_IMG

        base_param = [
            QueryParameters(
                img,
                "Continue",
                "Continue the last tracker.",
                ExtensionCustomAction(
                    partial(self.manager.continue_tracker, *args, **kwargs),
                    keep_app_open=False,
                ),
                SetUserQueryAction("tgl continue"),
            ),
        ]
        trackers = self.manager.create_list_actions(
            img=img,
            post_method=ExtensionCustomAction,
            custom_method=partial(
                self.manager.continue_tracker,
                *args,
                **kwargs,
            ),
            count_offset=-1,
            text_formatter="Continue {name} @{project}",
        )
        base_param.extend(trackers)

        return base_param

    def start_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = START_IMG
        base_param = [
            QueryParameters(
                img,
                "Start",
                "Start a new tracker.",
                ExtensionCustomAction(
                    partial(self.manager.start_tracker, *args, **kwargs),
                    keep_app_open=False,
                ),
                SetUserQueryAction("tgl start"),
            ),
        ]
        fresh_query = ["tgl", "start"]
        fresh_query.extend(args)
        trackers = self.manager.create_list_actions(
            img=img,
            post_method=self.manager.query_builder,
            count_offset=-1,
            text_formatter="Start {name} @{project}",
            query=fresh_query,
        )

        base_param.extend(trackers)

        return base_param

    def add_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        msg = "Add a new tracker"
        if args:
            msg += f" with description {args[0]}."
        else:
            msg += "."

        base_param = [
            QueryParameters(
                ADD_IMG,
                "Add",
                msg,
                ExtensionCustomAction(
                    partial(self.manager.add_tracker, *args, **kwargs),
                    keep_app_open=True,
                ),
                SetUserQueryAction("tgl add"),
            ),
        ]

        base_param.extend(self.generate_basic_hints())

        return base_param

    def check_current_tracker(self):
        if not isinstance(self.current_tracker, TogglTracker):
            return self.manager.generate_hint(
                "No active tracker is running.",
                SetUserQueryAction("tgl "),
                TipSeverity.ERROR,
                small=False,
            )

        return self.current_tracker

    def edit_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        track = self.check_current_tracker()
        if not isinstance(track, TogglTracker):
            return track

        params = [
            QueryParameters(
                track.find_color_svg(EDIT_IMG),
                track.description,
                "Edit the running tracker.",
                ExtensionCustomAction(
                    partial(self.manager.edit_tracker, *args, **kwargs),
                    keep_app_open=True,
                ),
                SetUserQueryAction("tgl edit"),
            ),
        ]
        data = self.create_tracker_subinfo(track)
        params.extend(self.manager.generate_hint(data))
        params.extend(self.generate_basic_hints())

        return params

    def create_tracker_subinfo(self, track: TogglTracker) -> tuple:
        data = [f"Started {track.start}"]
        if isinstance(track.project, str):
            data.append(f"{track.project}")

        if track.tags and isinstance(track.tags, (str, list)):
            if isinstance(track.tags, str):
                data.append(f"{track.tags}")
            else:
                data.append(", ".join(track.tags))
        return tuple(data)

    def stop_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        del args, kwargs
        track = self.check_current_tracker()
        if not isinstance(track, TogglTracker):
            return track

        params = [
            QueryParameters(
                STOP_IMG,
                "Stop",
                f"Stop tracking {track.description}.",
                ExtensionCustomAction(
                    partial(self.manager.stop_tracker),
                    keep_app_open=False,
                ),
                SetUserQueryAction("tgl stop"),
            ),
        ]

        data = self.create_tracker_subinfo(track)
        project = self.manager.generate_hint(data)
        params.extend(project)

        return params

    def remove_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        params = [
            QueryParameters(
                DELETE_IMG,
                "Delete",
                "Delete tracker.",
                ExtensionCustomAction(
                    partial(self.manager.remove_tracker, *args, **kwargs),
                    keep_app_open=False,
                ),
                SetUserQueryAction("tgl delete"),
            ),
        ]
        trackers = self.manager.create_list_actions(
            img=DELETE_IMG,
            post_method=ExtensionCustomAction,
            custom_method=partial(self.manager.remove_tracker),
            count_offset=-1,
            text_formatter="Delete tracker {name}",
        )

        params.extend(trackers)

        return params

    def total_trackers(self) -> list[QueryParameters]:
        params = QueryParameters(
            REPORT_IMG,
            "Generate Report",
            "View a weekly total of your trackers.",
            ExtensionCustomAction(
                partial(self.manager.total_trackers),
                keep_app_open=True,
            ),
            SetUserQueryAction("tgl report"),
        )
        return [params]

    def list_trackers(self, *args, **kwargs) -> list[QueryParameters]:
        params = QueryParameters(
            BROWSER_IMG,
            "List",
            f"View the last {self.max_results} trackers.",
            ExtensionCustomAction(
                partial(self.manager.list_trackers, *args, **kwargs),
                keep_app_open=True,
            ),
            SetUserQueryAction("tgl list"),
        )
        return [params]

    def get_projects(self, *args, **kwargs) -> list[QueryParameters]:
        # TODO: Implement more project actions
        data = QueryParameters(
            APP_IMG,
            "Projects",
            "View all your projects.",
            ExtensionCustomAction(
                partial(self.manager.list_projects, *args, **kwargs),
                keep_app_open=True,
            ),
            SetUserQueryAction("tgl project"),
        )
        return [data]

    @cache  # noqa: B019
    def generate_basic_hints(
        self,
        *,
        max_values: int = 3,
        default_action: BaseAction = DoNothingAction,
        **_,
    ) -> list[QueryParameters]:
        # TODO: Explore more clear html formatting.
        if not self.hints:
            return []

        default_action = default_action()

        hint_messages = (
            "Set a project with the @ symbol",
            "Add tags with the # symbol.",
            "Set the start and end time with > & < \
            respectively and the duration with both.",
            "If using spaces in your trackers or projects use quotation marks.",
            "Time formatting expects default TogglCli formatting.",
        )
        return self.manager.generate_hint(
            hint_messages[:max_values],
            action=default_action,
        )
