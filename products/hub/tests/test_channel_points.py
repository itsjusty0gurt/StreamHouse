import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.twitch.models import TwitchCustomReward
from products.hub.ui.channel_point_reward_dialog import ChannelPointRewardDialog


class ChannelPointRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_reward_model_reads_nested_twitch_settings(self) -> None:
        reward = TwitchCustomReward.from_dict(
            {
                "id": "reward-1",
                "title": "Hydrate",
                "cost": 500,
                "is_enabled": True,
                "max_per_stream_setting": {
                    "is_enabled": True,
                    "max_per_stream": 4,
                },
                "max_per_user_per_stream_setting": {
                    "is_enabled": True,
                    "max_per_user_per_stream": 1,
                },
                "global_cooldown_setting": {
                    "is_enabled": True,
                    "global_cooldown_seconds": 60,
                },
            },
            manageable=True,
        )

        self.assertTrue(reward.manageable)
        self.assertEqual(reward.max_per_stream, 4)
        self.assertEqual(reward.max_per_user_per_stream, 1)
        self.assertEqual(reward.global_cooldown_seconds, 60)

    def test_dialog_round_trips_reward_fields(self) -> None:
        reward = TwitchCustomReward(
            id="reward-1",
            title="Hydrate",
            cost=500,
            prompt="Drink some water",
            background_color="#123ABC",
            is_enabled=True,
            is_user_input_required=True,
            max_per_stream_enabled=True,
            max_per_stream=4,
            max_per_user_per_stream_enabled=True,
            max_per_user_per_stream=1,
            global_cooldown_enabled=True,
            global_cooldown_seconds=60,
            should_skip_request_queue=True,
            manageable=True,
        )
        dialog = ChannelPointRewardDialog(reward)

        values = dialog.values()

        self.assertEqual(values["title"], "Hydrate")
        self.assertEqual(values["cost"], 500)
        self.assertEqual(values["prompt"], "Drink some water")
        self.assertEqual(values["max_per_stream"], 4)
        self.assertEqual(values["global_cooldown_seconds"], 60)
        self.assertTrue(values["should_redemptions_skip_request_queue"])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
