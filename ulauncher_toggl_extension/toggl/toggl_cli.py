import json
import logging
import os
import re
import subprocess as sp
from abc import ABCMeta, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple, Optional

if TYPE_CHECKING:
    from main import TogglExtension
    from ulauncher_toggl_extension.toggl.toggl_manager import TogglManager


log = logging.getLogger(__name__)


class TogglTracker(NamedTuple):
    """Tuple for holding current tracker information while executing the
    rest of the script.
    """

    description: str
    entry_id: str
    stop: str
    project: Optional[str] = None
    start: Optional[str] = None
    duration: Optional[str] = None
    tags: Optional[list[str]] = None


class TogglCli(metaclass=ABCMeta):
    __slots__ = ("toggl_exec_path", "max_results", "workspace_id", "_cache_path")

    def __init__(
        self, config_path: Path, max_results: int, workspace_id: Optional[int] = None
    ) -> None:
        self.toggl_exec_path = str(config_path)
        self.max_results = max_results
        self.workspace_id = workspace_id

        self._cache_path = Path(__file__).parents[2] / "cache"
        self._cache_path.mkdir(parents=True, exist_ok=True)

    def base_command(self, cmd: list[str]) -> str:
        cmd.insert(0, self.toggl_exec_path)
        tcmd = " ".join(cmd)
        log.debug("Running subcommand: %s", tcmd)

        run = sp.run(
            tcmd,
            text=True,
            env=dict(os.environ),
            shell=True,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
        )

        return str(run.stdout.strip())

    def count_table(self, header: str) -> list[int]:
        RIGHT_ALIGNED = {"start", "stop", "duration"}

        count = []
        current_word = ""

        for index, letter in enumerate(header):
            right = current_word.strip().lower() in RIGHT_ALIGNED

            if (
                not right
                and current_word
                and current_word[-1] == " "
                and letter != " "
                and any(x.isalpha() for x in current_word)
            ):
                current_word = ""
                count.append(index)

            elif right and current_word[-1] != " " and letter == " ":
                current_word = ""
                count.append(index)

            current_word += letter

        return count

    def format_line(
        self, header_size: list[int], item: str, names: Optional[set] = None
    ) -> list[str] | None:
        prev = 0
        item_data = []
        for index in header_size:
            d = item[prev:index].strip()
            if isinstance(names, set) and d in names:
                return
            item_data.append(d)
            prev = index
        item_data.append(item[prev:].strip())
        return item_data

    def format_id_str(self, text: str) -> tuple[str, int]:
        name, item_id = text.split("(")

        item_id = re.sub(r"[\)\(#]", "", item_id)
        name = name.strip()

        return name, int(item_id)

    def cache_data(self, data: list) -> None:
        log.debug(f"Caching new data to {self.cache_path}")
        data = data.copy()
        data.insert(0, datetime.now())
        with self.cache_path.open("w", encoding="utf-8") as file:
            file.write(json.dumps(data, cls=CustomSerializer))

    def load_data(self) -> list | None:
        log.debug(f"Loading cached data from {self.cache_path}")
        with self.cache_path.open("r", encoding="utf-8") as file:
            data = json.loads(file.read(), cls=CustomDeserializer)

        date = data.pop(0)
        if datetime.now() - self.CACHE_LEN >= date:
            log.info("%s: Cache out of date. Will request new data.", self.__str__)
            return

        return data

    @property
    @abstractmethod
    def cache_path(self) -> Path:
        return self._cache_path

    @property
    @abstractmethod
    def CACHE_LEN(self) -> timedelta:
        return timedelta()


class TrackerCli(TogglCli):
    __slots__ = "latest_trackers"

    def __init__(
        self, config_path: Path, max_results: int, workspace_id: Optional[int] = None
    ) -> None:
        super().__init__(config_path, max_results, workspace_id)
        self.latest_trackers = []

    def list_trackers(
        self, *args, refresh: bool = False, **kwargs
    ) -> list[TogglTracker]:
        start_time = kwargs.get("start", False)
        end_time = kwargs.get("stop", False)
        times = start_time or end_time

        if not refresh and self.latest_trackers and not times:
            return self.latest_trackers

        if not refresh and self.cache_path.exists() and not times:
            data = self.load_data()
            if isinstance(data, list):
                return data
            refresh = True

        log.info("Refreshing Toggl tracker list.")
        cmd = [
            "ls",
            "--fields",
            "+project,+id,+tags",
            # BUG: Toggl CLI really slow when looking for projects
        ]
        if start_time:
            cmd.append("--start")
            cmd.append(start_time)

        if end_time:
            cmd.append("--stop")
            cmd.append(end_time)

        try:
            run = self.base_command(cmd).splitlines()
        except sp.CalledProcessError:
            log.error("Failed to retrieve tracker list. Returning last cache.")
            return self.latest_trackers

        header_size = self.count_table(run[0])
        self.latest_trackers = []
        checked_names = set()
        cnt = 1
        for item in run:
            if cnt == 1:
                cnt += 1
                continue
            item_data = self.format_line(header_size, item, checked_names)

            if item_data is None:
                continue

            desc, dur, start, stop, project, toggl_id, tags = item_data

            checked_names.add(desc)
            cnt += 1
            tracker = TogglTracker(
                description=desc.strip(),
                entry_id=toggl_id,
                stop=stop.strip(),
                project=project,
                duration=dur,
                start=start,
                tags=tags.split(", "),
            )
            self.latest_trackers.append(tracker)
            if cnt == self.max_results:
                break

        if refresh:
            self.cache_data(self.latest_trackers)

        return self.latest_trackers

    def check_running(self) -> TogglTracker | None:
        # TODO: Optimise this to use the latest list tracker call instead
        # for certain instances for more efficient usage

        cmd = ["now"]

        try:
            run = self.base_command(cmd)
        except sp.CalledProcessError:
            return

        if run == "There is no time entry running!":
            return

        lines = run.splitlines()

        desc, toggl_id = lines[0].split("#")
        _, duration = lines[2].split(": ", maxsplit=1)
        _, project = lines[3].split(": ", maxsplit=1)
        _, start = lines[4].split(": ", maxsplit=1)

        _, tags = lines[6].split(": ", maxsplit=1)
        tracker = TogglTracker(
            desc, toggl_id, "running", project, start, duration, tags.split(",")
        )

        return tracker

    def continue_tracker(self, *args, **kwargs) -> str:
        cmd = ["continue"]

        if args and isinstance(args[0], TogglTracker):
            desc = args[0].description
            cmd.append('"' + desc + '"')
        start = kwargs.get("start", False)
        if start:
            cmd.append("--start")
            cmd.append(start)

        return self.base_command(cmd)

    def stop_tracker(self) -> str:
        cmd = ["stop"]

        try:
            run = self.base_command(cmd)
        except sp.CalledProcessError as t:
            log.error("Stopping tracker unsucessful: %s", t)
            run = "Toggl tracker not running!"

        return run

    def start_tracker(self, tracker: TogglTracker) -> str:
        cmd = ["start", tracker.description]
        if tracker.tags is not None:
            cmd.append("--tags")
            tag_str = ",".join(tracker.tags)
            cmd.append(tag_str)
        if tracker.project is not None:
            cmd.append("--project")
            _, proj_id = self.format_id_str(tracker.project)
            cmd.append(str(proj_id))

        return self.base_command(cmd)

    def add_tracker(self, *args, **kwargs) -> str:
        start = kwargs.get("start", False)
        stop = kwargs.get("stop", False)
        if not start:
            return "Missing start date/time."
        if not stop:
            return "Missing stop time"

        desc = args[2:3] or False
        if not desc:
            return "No tracker description given."

        cmd = ["add", start, stop, desc]

        tags = kwargs.get("tags", False)
        if tags:
            cmd.append("--tags")
            cmd.append(",".join(tags))

        project = kwargs.get("project", False)
        if project:
            cmd.append("--project")
            if isinstance(project, str):
                _, pid = TogglProjects.project_name_formatter(str(project))
            else:
                pid = project
            cmd.append(str(pid))

        try:
            return self.base_command(cmd)
        except sp.CalledProcessError as p:
            log.error("Adding tracker with name %s was unsuccessful: %s", desc, p)
            return f"Adding tracker with name {desc} was unsuccessful."

    def edit_tracker(self, *args, **kwargs) -> str | None:
        cmd = ["now"]

        description = kwargs.get("description")
        if description is not None:
            cmd.append("--description")
            cmd.append(description)

        project = kwargs.get("project")
        if project is not None:
            cmd.append("--project")
            cmd.append(project)

        start = kwargs.get("start")
        if start is not None:
            cmd.append("--start")
            cmd.append(start)

        tags = kwargs.get("tags")
        if tags is not None:
            cmd.append("--tags")
            cmd.append(tags)

        if len(cmd) == 1:
            return

        try:
            return self.base_command(cmd)
        except sp.CalledProcessError as t:
            log.error("Tracker deletion error: %s", t)
            return "Tracker is current not running."

    def rm_tracker(self, tracker: int) -> str:
        cmd = ["rm", str(tracker)]

        try:
            return self.base_command(cmd)
        except sp.CalledProcessError as t:
            log.error("Tracker deletion error: %s", t)
            return "Tracker with id {tracker} does not exist!"

    def tracker_now(self) -> str:
        cmd = ["now"]
        return self.base_command(cmd)

    def sum_tracker(self) -> list[tuple[str, str]]:
        cmd = ["sum", "-st"]

        run = self.base_command(cmd).splitlines()

        days: list[tuple[str, str]] = []
        for i, item in enumerate(run):
            if i + 1 == self.max_results:
                break
            if i == 0:
                continue
            day = item[:12].strip()
            time = item[12:].strip()
            days.append((day, time))

        return days

    @property
    def cache_path(self) -> Path:
        return super().cache_path / Path("tracker_history.json")

    @property
    def CACHE_LEN(self) -> timedelta:
        return timedelta(days=1)


class TProject(NamedTuple):
    name: str
    project_id: int
    client: str
    color: str
    active: bool = True


class TogglProjects(TogglCli):
    __slots__ = "project_list"

    def __init__(
        self, config_path: Path, max_results: int, workspace_id: Optional[int] = None
    ) -> None:
        super().__init__(config_path, max_results, workspace_id)

        self.project_list = []

    def list_projects(
        self, *args, active: bool = True, refresh: bool = False, **kwargs
    ) -> list[TProject]:
        if not refresh and self.project_list:
            return self.project_list

        if not refresh and self.cache_path.exists():
            data = self.load_data()
            if isinstance(data, list):
                return data

        self.project_list = []

        cmd = ["ls", "-f", "+hex_color,+active"]

        run = self.base_command(cmd).splitlines()
        header_size = self.count_table(run[0])
        checked_names = set()
        cnt = 0
        for item in run:
            if cnt == 1:
                cnt += 1
                continue
            item_data = self.format_line(header_size, item, checked_names)

            if item_data is None:
                continue

            name, client, active_item, project_id, hex_color = item_data

            active_item = True if active_item == "True" else False

            if not active_item and active:
                continue

            checked_names.add(name)
            cnt += 1
            tracker = TProject(
                name=name,
                project_id=int(project_id),
                client=client,
                color=hex_color,
                active=active_item,
            )
            self.project_list.append(tracker)

        self.cache_data(self.project_list)

        return self.project_list

    def base_command(self, cmd: list[str]) -> str:
        cmd.insert(0, "projects")
        return super().base_command(cmd)

    @staticmethod
    def project_name_formatter(name: str) -> tuple[str, int]:
        name, project_id = name.split("(#")
        name = name.strip()
        return name, int(project_id[:-1])

    @property
    def cache_path(self) -> Path:
        return super().cache_path / Path("project_history.json")

    @property
    def CACHE_LEN(self) -> timedelta:
        return timedelta(weeks=2)


class CustomSerializer(json.JSONEncoder):
    def encode(  # pyright: ignore [reportIncompatibleMethodOverride]
        self, obj: Any
    ) -> str:
        if isinstance(obj, list):
            new_obj = []
            for item in obj:
                if isinstance(item, (TProject, TogglTracker)):
                    name = type(item).__name__
                    item = item._asdict()
                    item["data type"] = name
                new_obj.append(item)

            return super().encode(new_obj)
        return super().encode(obj)

    def default(self, obj):  # pyright: ignore [reportIncompatibleMethodOverride]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class CustomDeserializer(json.JSONDecoder):
    def decode(  # pyright: ignore [reportIncompatibleMethodOverride]
        self, obj, **kwargs
    ) -> Any:
        obj = super().decode(obj, **kwargs)

        decoded_obj = []
        for item in obj:
            if isinstance(item, dict):
                dt = item.get("data type")
                if dt is not None:
                    item.pop("data type")
                    if dt == "TProject":
                        item = TProject(**item)
                    elif dt == "TogglTracker":
                        item = TogglTracker(**item)

            elif isinstance(item, str):
                item = datetime.fromisoformat(item)
                decoded_obj.insert(0, item)
                continue

            decoded_obj.append(item)

        return decoded_obj
