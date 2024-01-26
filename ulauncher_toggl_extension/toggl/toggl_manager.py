import logging as log
from functools import cache, partial
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional

import gi

gi.require_version("Notify", "0.7")
from gi.repository import Notify
from ulauncher.api.shared.action.BaseAction import BaseAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction

# TODO: Integrate this instead of cli + as soon 3.12v exists for the API
## from toggl import api, tuils
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction

from ulauncher_toggl_extension import toggl
from ulauncher_toggl_extension.toggl.toggl_cli import (
    TogglProjects,
    TogglTracker,
    TProject,
    TrackerCli,
)

# from ulauncher_toggl_extension import utils
# utils.ensure_import("togglcli")

if TYPE_CHECKING:
    from main import TogglExtension

APP_IMG = Path("images/icon.svg")
START_IMG = Path("images/start.svg")
EDIT_IMG = Path("images/edit.svg")
ADD_IMG = Path("images/add.svg")  # TODO: Needs to be created.
STOP_IMG = Path("images/stop.svg")
DELETE_IMG = Path("images/delete.svg")
CONTINUE_IMG = Path("images/continue.svg")
REPORT_IMG = Path("images/reports.svg")
BROWSER_IMG = Path("images/browser.svg")


class QueryParameters(NamedTuple):
    icon: Path
    name: str
    description: str
    on_enter: BaseAction
    on_alt_enter: Optional[BaseAction] = None


class NotificationParameters(NamedTuple):
    body: str
    icon: Path
    title: str = "Toggl Extension"


class TogglViewer:
    __slots__ = (
        "config_path",
        "max_results",
        "default_project",
        "tcli",
        "manager",
        "extension",
    )

    def __init__(self, ext: "TogglExtension") -> None:
        self.config_path = ext.config_path
        self.max_results = ext.max_results
        self.default_project = ext.default_project

        self.tcli = TrackerCli(self.config_path, self.max_results, self.default_project)
        self.manager = TogglManager(
            self.config_path, self.max_results, self.default_project
        )

    def default_options(self, *args, **kwargs) -> list[QueryParameters]:
        BASIC_TASKS = [
            QueryParameters(
                START_IMG,
                "Start",
                "Start a Toggl tracker",
                SetUserQueryAction("tgl stt"),
            ),
            QueryParameters(
                STOP_IMG,
                "Stop",
                "Stop the current Toggl tracker",
                ExtensionCustomAction(
                    partial(self.manager.stop_tracker, *args),
                    keep_app_open=False,
                ),
            ),
            QueryParameters(
                START_IMG,
                "Add",
                "Add a toggl time tracker at a specified time.",
                SetUserQueryAction("tgl add"),
            ),
            QueryParameters(
                DELETE_IMG,
                "Delete",
                "Delete a Toggl time tracker",
                SetUserQueryAction("tgl rm"),
            ),
            QueryParameters(
                REPORT_IMG,
                "Report",
                "View a report of previous week of trackers.",
                ExtensionCustomAction(
                    partial(self.manager.total_trackers), keep_app_open=True
                ),
            ),
            QueryParameters(
                BROWSER_IMG,
                "List",
                f"View the last {self.max_results} trackers.",
                ExtensionCustomAction(
                    partial(self.manager.list_trackers), keep_app_open=True
                ),
            ),
            QueryParameters(
                APP_IMG,
                "Projects",
                "View & Edit projects.",
                ExtensionCustomAction(
                    partial(self.manager.list_projects, *args), keep_app_open=True
                ),
            ),
        ]
        current_tracker = self.tcli.check_running()
        if current_tracker is None:
            current = QueryParameters(
                CONTINUE_IMG,
                "Continue",
                "Continue the latest Toggl time tracker",
                ExtensionCustomAction(partial(self.manager.continue_tracker)),
                SetUserQueryAction("tgl continue"),
            )
        else:
            current = QueryParameters(
                APP_IMG,
                f"Currently Running: {current_tracker.description}",
                f"Since: {current_tracker.start} @{current_tracker.project}",
                ExtensionCustomAction(
                    partial(self.edit_tracker, current=current_tracker),
                    keep_app_open=True,
                ),
            )

        BASIC_TASKS.insert(0, current)

        return BASIC_TASKS

    def continue_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = CONTINUE_IMG

        base_param = [
            QueryParameters(
                img,
                "Continue",
                "Continue the last tracker.",
                ExtensionCustomAction(
                    partial(self.manager.continue_tracker, *args),
                    keep_app_open=False,
                ),
            )
        ]
        trackers = self.manager.create_list_actions(
            img=img,
            post_method=ExtensionCustomAction,
            custom_method=partial(self.manager.continue_tracker),
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
                    partial(self.manager.start_tracker),
                    keep_app_open=False,
                ),
            )
        ]

        trackers = self.manager.create_list_actions(
            img=img,
            post_method=ExtensionCustomAction,
            custom_method=partial(self.manager.start_tracker),
            count_offset=-1,
            text_formatter="Start {name} @{project}",
        )

        base_param.extend(trackers)

        return base_param

    def add_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = EDIT_IMG
        base_param = QueryParameters(
            img,
            "Add",
            "Add a new tracker.",
            ExtensionCustomAction(
                partial(self.manager.add_tracker, *args),
                keep_app_open=True,
            ),
        )

        return [base_param]

    def edit_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = EDIT_IMG
        tracker = kwargs["current"]
        if tracker is None:
            return SetUserQueryAction("tgl ")

        params = QueryParameters(
            img,
            tracker.description,
            "Edit the running tracker.",
            ExtensionCustomAction(
                partial(self.manager.edit_tracker, *args, **kwargs), keep_app_open=True
            ),
        )
        return [params]

    def stop_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = STOP_IMG
        params = QueryParameters(
            img,
            "Stop",
            "Stop the current tracker.",
            ExtensionCustomAction(
                partial(self.manager.stop_tracker, *args),
                keep_app_open=False,
            ),
        )
        return [params]

    def remove_tracker(self, *args, **kwargs) -> list[QueryParameters]:
        img = DELETE_IMG
        params = [
            QueryParameters(
                img,
                "Delete",
                "Delete tracker.",
                ExtensionCustomAction(
                    partial(self.manager.remove_tracker, *args),
                    keep_app_open=False,
                ),
            )
        ]
        trackers = self.manager.create_list_actions(
            img=img,
            post_method=ExtensionCustomAction,
            custom_method=partial(self.manager.remove_tracker),
            count_offset=-1,
            text_formatter="Delete tracker {name}",
        )

        params.extend(trackers)

        return params

    def total_trackers(self, *args, **kwargs) -> list[QueryParameters]:
        img = REPORT_IMG

        params = QueryParameters(
            img,
            "Generate Report",
            "View a weekly total of your trackers.",
            ExtensionCustomAction(
                partial(self.manager.total_trackers, *args), keep_app_open=True
            ),
        )
        return [params]

    def list_trackers(
        self, *args, post_method: Optional[MethodType] = None, **kwargs
    ) -> list[QueryParameters]:
        img = BROWSER_IMG
        params = QueryParameters(
            img,
            "List",
            f"View the last {self.max_results} trackers.",
            ExtensionCustomAction(
                partial(self.manager.list_trackers, *args), keep_app_open=True
            ),
        )
        return [params]

    def get_projects(self, *args, **kwargs) -> list[QueryParameters]:
        img = APP_IMG
        data = QueryParameters(
            img,
            "Projects",
            "View & Edit projects.",
            ExtensionCustomAction(
                partial(self.manager.list_projects, *args, **kwargs), keep_app_open=True
            ),
        )
        return [data]


class TogglManager:
    __slots__ = (
        "config_path",
        "max_results",
        "workspace_id",
        "tcli",
        "pcli",
        "notification",
    )

    def __init__(
        self, config_path: Path, max_results: int, default_project: int | None
    ) -> None:
        self.config_path = config_path
        self.max_results = max_results
        self.workspace_id = default_project

        self.tcli = TrackerCli(self.config_path, self.max_results, self.workspace_id)
        self.pcli = TogglProjects(self.config_path, self.max_results, self.workspace_id)

        self.notification = None

    def continue_tracker(self, *args) -> bool:
        img = CONTINUE_IMG

        cnt = self.tcli.continue_tracker(*args)
        noti = NotificationParameters(cnt, img)

        self.show_notification(noti)
        return True

    def start_tracker(self, *args) -> bool:
        img = START_IMG

        print(*args)

        if not args or not isinstance(args[0], TogglTracker):
            return False

        cnt = self.tcli.start_tracker(args[0])
        noti = NotificationParameters(cnt, img)

        self.show_notification(noti)
        return True

    def add_tracker(self, *args, **kwargs) -> bool:
        img = START_IMG
        msg = self.tcli.add_tracker(*args, **kwargs)
        noti = NotificationParameters(msg, img)
        self.show_notification(noti)

        return True

    def edit_tracker(self, *args, **kwargs) -> bool:
        img = EDIT_IMG

        msg = self.tcli.edit_tracker(*args, **kwargs)
        if msg == "Tracker is current not running." or msg is None:
            return False

        noti = NotificationParameters(msg, img)
        self.show_notification(noti)

        return True

    def stop_tracker(self, *args) -> bool:
        img = STOP_IMG
        msg = self.tcli.stop_tracker()

        noti = NotificationParameters(str(msg), img)
        self.show_notification(noti)
        return True

    def remove_tracker(self, toggl_id: int | TogglTracker) -> bool:
        if isinstance(toggl_id, TogglTracker):
            toggl_id = int(toggl_id.entry_id)
        elif not isinstance(toggl_id, int):
            return False

        img = DELETE_IMG

        cnt = self.tcli.rm_tracker(tracker=toggl_id)
        noti = NotificationParameters(cnt, img)

        self.show_notification(noti)
        return True

    def total_trackers(self, *args) -> list[QueryParameters]:
        img = REPORT_IMG

        data = self.tcli.sum_tracker()
        queries = []
        for day, time in data:
            param = QueryParameters(img, day, time, DoNothingAction())
            # TODO: Possibly could show a break down of the topx trackers for
            # that given day the future instead of nothing.
            queries.append(param)

        return queries

    def list_trackers(
        self,
        *args,
    ) -> list[QueryParameters]:
        img = REPORT_IMG

        return self.create_list_actions(img, refresh="refresh" in args)

    def list_projects(self, *args, **kwargs) -> list[QueryParameters]:
        img = APP_IMG
        data = self.create_list_actions(
            img,
            text_formatter="Client: {client}",
            data_type="project",
            refresh="refresh" in args,
        )
        return data

    def tracker_builder(
        self, img: Path, meth: MethodType, text_formatter: str, tracker: TogglTracker
    ) -> QueryParameters | None:
        text = tracker.stop
        if tracker.stop != "running":
            text = text_formatter.format(
                stop=tracker.stop,
                tid=tracker.entry_id,
                name=tracker.description,
                project=tracker.project,
                tags=tracker.tags,
                start=tracker.start,
                duration=tracker.duration,
            )
        else:
            return

        param = QueryParameters(
            img,
            tracker.description,
            text,
            meth,
        )
        return param

    def project_builder(
        self, img: Path, meth: MethodType, text_formatter: str, project: TProject
    ) -> QueryParameters:
        text = text_formatter.format(
            name=project.name,
            project_id=project.project_id,
            client=project.client,
            color=project.color,
            active=project.active,
        )
        param = QueryParameters(img, project.name, text, meth)
        return param

    def create_list_actions(
        self,
        img: Path,
        post_method=DoNothingAction,
        custom_method: Optional[partial] = None,
        count_offset: int = 0,
        text_formatter: str = "Stopped: {stop}",
        keep_open: bool = False,
        refresh: bool = False,
        data_type: str = "tracker",
    ) -> list[QueryParameters]:
        if data_type == "tracker":
            list_data = self.tcli.list_trackers(refresh)
        else:
            list_data = self.pcli.list_projects(refresh=refresh)

        queries = []

        for i, data in enumerate(list_data, start=1):
            if self.max_results - count_offset == i:
                break

            if custom_method is not None:
                func = partial(custom_method, data)
                meth = post_method(func, keep_app_open=keep_open)
            else:
                meth = post_method()

            if isinstance(data, TogglTracker):
                param = self.tracker_builder(img, meth, text_formatter, data)
            else:
                param = self.project_builder(img, meth, text_formatter, data)

            if param is None:
                continue

            queries.append(param)

        return queries

    def show_notification(
        self, data: NotificationParameters, on_close: Optional[Callable] = None
    ) -> None:
        icon = str(Path(__file__).parents[2] / data.icon)
        if not Notify.is_initted():
            Notify.init("TogglExtension")
        if self.notification is None:
            self.notification = Notify.Notification.new(data.title, data.body, icon)
        else:
            self.notification.update(data.title, data.body, icon)
        if on_close is not None:
            self.notification.connect("closed", on_close)
        self.notification.show()
