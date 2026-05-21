"""
粉爪大劫案 收益统计
- 撤离成功后截图OCR识别粉爪积分和爪爪币
- 累计统计并通过 focus 推送到前端
"""

import re
import time
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


class PinkPawRewardTracker:
    """全局收益追踪器"""
    _success_count: int = 0
    _fail_count: int = 0
    _total_score: int = 0      # 方斯（藏品价值）
    _total_coins: int = 0      # 爪爪币
    _total_points: int = 0     # 粉爪积分
    _initialized: bool = False

    @classmethod
    def reset(cls):
        cls._success_count = 0
        cls._fail_count = 0
        cls._total_score = 0
        cls._total_coins = 0
        cls._total_points = 0

    @classmethod
    def on_success(cls, score: int = 0, coins: int = 0, points: int = 0):
        cls._success_count += 1
        cls._total_score += score
        cls._total_coins += coins
        cls._total_points += points

    @classmethod
    def on_fail(cls):
        cls._fail_count += 1

    @classmethod
    def get_msg(cls, success: bool, score: int = 0, coins: int = 0, points: int = 0) -> str:
        total_runs = cls._success_count + cls._fail_count
        if success:
            msg = f"✅ 撤离成功！第{total_runs}局"
            if score > 0 or coins > 0 or points > 0:
                msg += f"（本局：方斯{score} 爪爪币{coins} 积分{points}）"
            msg += f"，累计：方斯{cls._total_score} 爪爪币{cls._total_coins} 积分{cls._total_points}"
        else:
            msg = f"❌ 撤离失败。第{total_runs}局（成功{cls._success_count}/失败{cls._fail_count}）"
        return msg

    @classmethod
    def get_summary(cls) -> str:
        return f"🐾 粉爪大劫案: {cls._success_count}局成功/{cls._fail_count}局失败 | 累计方斯{cls._total_score} 爪爪币{cls._total_coins} 积分{cls._total_points}"


def _ocr_number(context: Context, image, roi: list) -> int:
    """对指定ROI做OCR，提取数字"""
    reco_detail = context.run_recognition(
        "PinkPaw_OCR_Reward",
        image,
        pipeline_override={
            "PinkPaw_OCR_Reward": {
                "recognition": "OCR",
                "roi": roi,
                "expected": [],
                "only_rec": False
            }
        }
    )
    if reco_detail and reco_detail.all_results:
        text = reco_detail.all_results[0].text if hasattr(reco_detail.all_results[0], 'text') else ""
        # 提取数字（去掉逗号等）
        nums = re.sub(r'[^\d]', '', text)
        return int(nums) if nums else 0
    return 0


def notify_pinkpaw_reward(context: Context, success: bool, fansi: int = 0, pinkcoins: int = 0):
    """
    撤离后调用。success=True时会截图OCR读取收益。
    fansi/pinkcoins 参数保留兼容但优先使用OCR结果。
    """
    if not PinkPawRewardTracker._initialized:
        PinkPawRewardTracker.reset()
        PinkPawRewardTracker._initialized = True

    score = 0
    coins = 0
    points = 0

    if success:
        # 截图（调用方应在结算界面出现后再调用此函数）
        image = context.tasker.controller.post_screencap().wait().get()
        # OCR 读取三项收益
        score = _ocr_number(context, image, [640, 255, 150, 35])    # 方斯（本局藏品价值）
        points = _ocr_number(context, image, [640, 480, 150, 35])   # 粉爪积分
        coins = _ocr_number(context, image, [640, 520, 150, 35])    # 爪爪币
        PinkPawRewardTracker.on_success(score, coins, points)
    else:
        PinkPawRewardTracker.on_fail()

    msg = PinkPawRewardTracker.get_msg(success, score, coins, points)

    try:
        context.override_pipeline({
            "PinkPawReward_Notify": {
                "recognition": "DirectHit",
                "action": "DoNothing",
                "focus": {
                    "Node.Action.Starting": msg
                }
            }
        })
        context.run_task("PinkPawReward_Notify")
    except Exception:
        pass


@AgentServer.custom_action("pinkpaw_read_reward")
class PinkPawReadReward(CustomAction):
    """在确认撤离弹窗上先OCR读取收益，再点击确认撤离"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        if not PinkPawRewardTracker._initialized:
            PinkPawRewardTracker.reset()
            PinkPawRewardTracker._initialized = True

        # 当前画面就是确认撤离弹窗，直接截图读取收益
        image = context.tasker.controller.post_screencap().wait().get()
        score = _ocr_number(context, image, [430, 395, 130, 35])    # 本局收益（方斯）
        coins = _ocr_number(context, image, [770, 395, 100, 35])    # 爪爪币

        PinkPawRewardTracker.on_success(score, coins, 0)
        msg = PinkPawRewardTracker.get_msg(True, score, coins, 0)

        # 推送到前端
        try:
            context.override_pipeline({
                "PinkPawReward_Notify": {
                    "recognition": "DirectHit",
                    "action": "DoNothing",
                    "focus": {
                        "Node.Action.Starting": msg
                    }
                }
            })
            context.run_task("PinkPawReward_Notify")
        except Exception:
            pass

        # 点击"确认撤离"按钮
        context.tasker.controller.post_click(648, 458).wait()

        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("pinkpaw_reward_summary")
class PinkPawRewardSummary(CustomAction):
    """任务完全结束时上报汇总"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        summary = PinkPawRewardTracker.get_summary()
        try:
            context.override_pipeline({
                "PinkPawReward_Summary": {
                    "recognition": "DirectHit",
                    "action": "DoNothing",
                    "focus": {
                        "Node.Action.Starting": summary
                    }
                }
            })
            context.run_task("PinkPawReward_Summary")
        except Exception:
            pass

        PinkPawRewardTracker.reset()
        PinkPawRewardTracker._initialized = False
        return CustomAction.RunResult(success=True)
