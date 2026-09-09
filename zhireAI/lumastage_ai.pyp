"""挚热AI渲染器 command plugin entry point."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import shutil
from pathlib import Path
from typing import Optional

import c4d
from c4d import plugins


PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lumastage.constants import (  # noqa: E402
    PLUGIN_ID,
    PRODUCT_WINDOW_TITLE,
)
from lumastage.protection import verify_installation  # noqa: E402


def _load_juaihub_adapter():
    """Load the companion before the UI imports ImageApiClient.

    Cinema 4D does not guarantee that multiple top-level ``.pyp`` files in a
    plug-in folder are executed in filename order. Loading this companion here
    makes the JuAIHub route deterministic while its own patch guard keeps a
    later host scan harmless.
    """

    module_name = "_zhire_juaihub_adapter"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = PLUGIN_ROOT / "zz_juaihub_adapter.pyp"
    loader = importlib.machinery.SourceFileLoader(module_name, str(source))
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise ImportError("无法创建挚热 API 适配器加载规范")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


JUAIHUB_ADAPTER = _load_juaihub_adapter()
from lumastage.ui import StudioDialog  # noqa: E402


INSTALLATION_INTEGRITY = verify_installation(PLUGIN_ROOT)


def _data_directory() -> Path:
    try:
        preferences = Path(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS))
    except Exception:
        preferences = PLUGIN_ROOT
    path = preferences / "zhireAI"
    legacy_path = preferences / "LumaStageAI"
    if not path.exists() and legacy_path.is_dir():
        try:
            shutil.copytree(str(legacy_path), str(path))
        except Exception:
            path = legacy_path
    path.mkdir(parents=True, exist_ok=True)
    return path


class LumaStageCommand(plugins.CommandData):
    dialog: Optional[StudioDialog] = None
    integrity_warning_shown = False

    def _dialog(self) -> StudioDialog:
        if self.dialog is None:
            self.dialog = StudioDialog(_data_directory())
        return self.dialog

    def Execute(self, doc: c4d.documents.BaseDocument) -> bool:
        if not INSTALLATION_INTEGRITY[0] and not self.integrity_warning_shown:
            self.integrity_warning_shown = True
            c4d.gui.MessageDialog(
                "当前插件文件不完整或已被修改。\n{}\n"
                "请重新安装官方发布包。\n"
                "该检查仅读取本地插件文件，不上传任何用户数据。".format(
                    INSTALLATION_INTEGRITY[1]
                )
            )
        dialog = self._dialog()
        if dialog.IsOpen() and not dialog.GetFolding():
            dialog.SetFolding(True)
        else:
            dialog.Open(
                c4d.DLG_TYPE_ASYNC,
                PLUGIN_ID,
                defaultw=340,
                defaulth=780,
            )
        return True

    def RestoreLayout(self, secret) -> bool:
        return self._dialog().Restore(PLUGIN_ID, secret)

    def GetState(self, doc: c4d.documents.BaseDocument) -> int:
        state = c4d.CMD_ENABLED
        if self.dialog is not None and self.dialog.IsOpen() and not self.dialog.GetFolding():
            state |= c4d.CMD_VALUE
        return state


COMMAND = LumaStageCommand()


def PluginMessage(message_id: int, data) -> bool:
    if message_id == c4d.C4DPL_ENDACTIVITY and COMMAND.dialog is not None:
        worker = COMMAND.dialog.state.worker
        if worker is not None:
            worker.End()
    return True


if __name__ == "__main__":
    plugins.RegisterCommandPlugin(
        id=PLUGIN_ID,
        str=PRODUCT_WINDOW_TITLE,
        info=0,
        icon=None,
        help="{}：从 C4D 场景与参考图生成 AI 效果图。".format(
            PRODUCT_WINDOW_TITLE
        ),
        dat=COMMAND,
    )
