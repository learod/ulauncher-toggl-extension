from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.event import ItemEnterEvent, KeywordQueryEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem

from ulauncher_toggl_extension.toggl import *


class TogglExtension(Extension):
    def __init__(self):
        super().__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())


class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
        items = []
        for i in range(5):
            items.append(
                ExtensionResultItem(
                    icon="images/icon.png",
                    name="Item %s" % i,
                    description="Item description %s" % i,
                    on_enter=HideWindowAction(),
                )
            )

        return RenderResultListAction(items)


if __name__ == "__main__":
    TogglExtension().run()
