import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("orchestrator.py")
SPEC = importlib.util.spec_from_file_location("sky_prompt_orchestrator", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
orchestrator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orchestrator
SPEC.loader.exec_module(orchestrator)


class OrchestratorTests(unittest.TestCase):
    def test_tool_executor_routes_commands_to_tool_tab_alias(self) -> None:
        executor = orchestrator.ToolExecutor(["npx", "mcporter"], tool_tab_alias="sky-tools")

        command = executor._build_cmd({"tool": "NAVIGATE", "args": "amazon.com"})

        self.assertEqual(command[:3], ["npx", "mcporter", "call"])
        self.assertIn("unchainedsky.cdp_navigate", command)
        self.assertIn("url:https://amazon.com", command)
        self.assertIn("tab_id:sky-tools", command)

    def test_prompt_command_routes_sky_to_chat_tab_alias(self) -> None:
        executor = orchestrator.ToolExecutor(["npx", "mcporter"], tool_tab_alias="sky-tools")
        agent = orchestrator.ReactiveAgent(
            sky_command=["sky"],
            executor=executor,
            chat_tab_alias="sky-chat",
            unchained_port=9333,
        )

        command = agent._build_prompt_cmd("What next?")

        self.assertEqual(
            command,
            [
                "sky",
                "--browser-tab",
                "sky-chat",
                "--unchained-port",
                "9333",
                "--output-format",
                "plain",
                "-p",
                "What next?",
            ],
        )

    def test_ensure_isolated_tabs_recreates_tool_alias_when_it_points_to_chat_tab(self) -> None:
        manager = orchestrator.UnchainedTabManager(["unchained"], port=9222)
        manager.ensure_browser = mock.Mock()
        manager.list_tabs = mock.Mock(
            side_effect=[
                [{"id": "chat-1", "url": "https://chatgpt.com/c/abc"}],
                [{"id": "chat-1", "url": "https://chatgpt.com/c/abc"}],
            ]
        )
        manager.list_aliases = mock.Mock(
            side_effect=[
                {"sky-chat": "chat-1", "sky-tools": "chat-1"},
                {"sky-chat": "chat-1", "sky-tools": "chat-1"},
            ]
        )
        manager.create_tab = mock.Mock(return_value={"id": "tool-2", "url": "about:blank"})
        manager.set_alias = mock.Mock()

        setup = manager.ensure_isolated_tabs()

        self.assertEqual(setup.chat_tab_id, "chat-1")
        self.assertEqual(setup.tool_tab_id, "tool-2")
        manager.create_tab.assert_called_once_with("about:blank")
        manager.set_alias.assert_called_once_with("sky-tools", "tool-2")

    def test_ensure_isolated_tabs_creates_chat_tab_when_alias_is_missing_or_non_chat(self) -> None:
        manager = orchestrator.UnchainedTabManager(["unchained"], port=9222)
        manager.ensure_browser = mock.Mock()
        manager.list_tabs = mock.Mock(
            side_effect=[
                [{"id": "tab-0", "url": "https://amazon.com"}],
                [
                    {"id": "tab-0", "url": "https://amazon.com"},
                    {"id": "chat-2", "url": "https://chatgpt.com/c/123"},
                ],
            ]
        )
        manager.list_aliases = mock.Mock(side_effect=[{}, {"sky-chat": "chat-2"}])
        manager.create_tab = mock.Mock(
            side_effect=[
                {"id": "chat-2", "url": "https://chatgpt.com"},
                {"id": "tool-3", "url": "about:blank"},
            ]
        )
        manager.set_alias = mock.Mock()

        setup = manager.ensure_isolated_tabs()

        self.assertEqual(setup.chat_tab_id, "chat-2")
        self.assertEqual(setup.tool_tab_id, "tool-3")
        self.assertEqual(
            manager.set_alias.call_args_list,
            [mock.call("sky-chat", "chat-2"), mock.call("sky-tools", "tool-3")],
        )

    def test_main_prepare_tabs_only_prints_alias_mapping(self) -> None:
        fake_manager = mock.Mock()
        fake_manager.ensure_isolated_tabs.return_value = orchestrator.TabSetup(
            chat_alias="sky-chat",
            chat_tab_id="chat-1",
            tool_alias="sky-tools",
            tool_tab_id="tool-1",
        )

        with mock.patch.object(orchestrator, "UnchainedTabManager", return_value=fake_manager):
            stdout_buffer = io.StringIO()
            with mock.patch("sys.stdout", stdout_buffer):
                result = orchestrator.main(["--prepare-tabs-only"])

        self.assertEqual(result, 0)
        rendered = stdout_buffer.getvalue()
        self.assertIn("tabs ready: sky-chat=chat-1 sky-tools=tool-1", rendered)


if __name__ == "__main__":
    unittest.main()
