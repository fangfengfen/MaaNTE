"""
粉爪大劫案 收益统计
- 撤离成功时，OCR 识别"安全撤离"结算界面的方斯（藏品价值）和爪爪币
- 首局校准：连续截图找到结算界面出现时机，后续局复用
- 撤离失败不计
- 通过 focus 推送到前端
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


def _extract_number(text: str) -> int | None:
    """从 OCR 文本中提取数字（支持逗号分隔如 1,234，支持 x 前缀如 x5,120）"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _ocr_region(context: Context, image, node_name: str, roi: list) -> str:
    """OCR 识别指定区域，返回拼接的文本"""
    reco_detail = context.run_recognition(
        node_name,
        image,
        pipeline_override={
            node_name: {
                "recognition": "OCR",
                "roi": roi,
                "expected": [],
                "only_rec": False,
            }
        },
    )
    if reco_detail and reco_detail.all_results:
        texts = []
        for r in reco_detail.all_results:
            if hasattr(r, "text") and r.text.strip():
                texts.append(r.text.strip())
        return " ".join(texts)
    return ""


class PinkPawRewardTracker:
    """全局收益追踪器"""
    _success_count: int = 0
    _fail_count: int = 0
    _total_fansi: int = 0
    _total_pinkcoins: int = 0
    _initialized: bool = False
    _calibrated_delay: float = 0  # 校准后的最佳延迟（秒），0 = 未校准

    # ==========================================================
    # OCR 区域配置（基于 1280x720 分辨率）
    # "安全撤离" 结算界面:
    #   本局藏品价值  [图标] x276,570  (y≈235)  ← 方斯
    #   爪爪币        [图标] x1,128    (y≈385)
    # ==========================================================
    ROI_FANGS_LABEL = [340, 220, 190, 35]
    ROI_FANGS_VALUE = [530, 220, 200, 35]
    ROI_COINS_LABEL = [290, 370, 130, 35]
    ROI_COINS_VALUE = [580, 370, 170, 35]
    ROI_REWARD_PANEL = [280, 200, 470, 210]
    ROI_TITLE = [430, 85, 250, 55]  # "安全撤离" 标题

    @classmethod
    def reset(cls):
        cls._success_count = 0
        cls._fail_count = 0
        cls._total_fansi = 0
        cls._total_pinkcoins = 0
        cls._calibrated_delay = 0

    @classmethod
    def on_evacuate_success(cls, fansi: int = 0, pinkcoins: int = 0):
        cls._success_count += 1
        cls._total_fansi += fansi
        cls._total_pinkcoins += pinkcoins

    @classmethod
    def on_evacuate_fail(cls):
        cls._fail_count += 1

    @classmethod
    def get_msg(cls) -> str:
        msg = f"第{cls._success_count + cls._fail_count}局"
        if cls._success_count > 0 or cls._fail_count > 0:
            msg += f"（成功{cls._success_count}/失败{cls._fail_count}）"
        if cls._total_fansi > 0:
            msg += f"，累计方斯{cls._total_fansi}"
        if cls._total_pinkcoins > 0:
            msg += f"，累计爪爪币{cls._total_pinkcoins}"
        return msg

    @classmethod
    def get_summary(cls) -> str:
        parts = [f"粉爪大劫案: {cls._success_count}局成功/{cls._fail_count}局失败"]
        if cls._total_fansi > 0:
            parts.append(f"方斯{cls._total_fansi}")
        if cls._total_pinkcoins > 0:
            parts.append(f"爪爪币{cls._total_pinkcoins}")
        return " | ".join(parts)

    # ---- OCR 识别方法 ----

    @classmethod
    def _get_debug_dir(cls) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        ))))
        return os.path.join(base_dir, "debug_screenshots", "pinkpaw_rewards")

    @classmethod
    def _ocr_labeled_value(cls, context: Context, image,
                           label_roi: list, value_roi: list, label_text: str) -> int | None:
        """先 OCR 标签确认，再 OCR 数值"""
        label_result = _ocr_region(context, image, f"PPH_OCR_{label_text}_L", label_roi)
        label_keywords = {
            "本局藏品价值": ["藏品", "价值", "藏品价值"],
            "爪爪币": ["爪爪", "爪币", "币"],
        }
        keywords = label_keywords.get(label_text, [label_text])
        if not any(kw in label_result for kw in keywords):
            print(f"[PinkPawReward] 标签'{label_text}'未命中, OCR='{label_result}'")
            return None
        value_text = _ocr_region(context, image, f"PPH_OCR_{label_text}_V", value_roi)
        print(f"[PinkPawReward] {label_text} 数值OCR='{value_text}'")
        return _extract_number(value_text)

    @classmethod
    def _ocr_panel_fallback(cls, context: Context, image) -> tuple[int | None, int | None]:
        """备用方案：OCR 整个结算区域，用正则解析"""
        full_text = _ocr_region(context, image, "PPH_OCR_Panel", cls.ROI_REWARD_PANEL)
        if not full_text:
            print("[PinkPawReward] panel fallback: OCR 无结果")
            return None, None
        print(f"[PinkPawReward] panel OCR: '{full_text}'")

        fangs, coins = None, None
        m_fangs = re.search(r"(?:藏品|价值)[^\d]*[x×]?\s*(\d[\d,]*)", full_text)
        if m_fangs:
            fangs = _extract_number(m_fangs.group(1))
        m_coins = re.search(r"(?:爪爪币|爪币|爪爪)[^\d]*[x×]?\s*(\d[\d,]*)", full_text)
        if m_coins:
            coins = _extract_number(m_coins.group(1))
        if fangs is None and coins is None:
            numbers = re.findall(r"[x×]?\s*(\d[\d,]+)", full_text)
            extracted = [_extract_number(n) for n in numbers if _extract_number(n) is not None and _extract_number(n) > 0]
            if len(extracted) >= 2:
                fangs, coins = extracted[0], extracted[1]
                print(f"[PinkPawReward] 按顺序提取: 方斯={fangs}, 爪爪币={coins}")
            elif len(extracted) == 1:
                fangs = extracted[0]
        return fangs, coins

    @classmethod
    def _calibrate_and_capture(cls, context: Context):
        """
        首局校准：确认撤离后连续截图 6 秒（每 0.5s 一张），
        全部保存，找到第一张有结算界面的截图，记住最佳延迟。
        """
        save_dir = cls._get_debug_dir()
        os.makedirs(save_dir, exist_ok=True)

        interval, total = 0.5, 6.0
        num_shots = int(total / interval)
        print(f"[PinkPawReward] 首局校准：连续截图{total}秒，间隔{interval}秒")
        start = time.time()
        best_image, best_delay = None, 0.0

        for i in range(num_shots):
            time.sleep(interval)
            elapsed = time.time() - start
            img = context.tasker.controller.post_screencap().wait().get()
            if img is None or not isinstance(img, np.ndarray):
                continue
            is_black = img.mean() < 5
            status = "黑屏" if is_black else "有画面"
            ts = datetime.now().strftime("%H%M%S")
            try:
                cv2.imwrite(os.path.join(save_dir, f"calibrate_{elapsed:.1f}s_{status}_{ts}.png"), img)
            except Exception:
                pass
            print(f"[PinkPawReward]   [{elapsed:.1f}s] {status} (mean={img.mean():.1f})")
            if is_black:
                continue
            if best_image is None:
                title = _ocr_region(context, img, "PPH_OCR_Title", cls.ROI_TITLE)
                if "撤离" in title or "安全" in title:
                    best_image, best_delay = img, elapsed
                    print(f"[PinkPawReward]   [{elapsed:.1f}s] ★ 检测到结算界面!")

        if best_image is not None:
            cls._calibrated_delay = best_delay + 0.5
            print(f"[PinkPawReward] 校准完成: 使用延迟={cls._calibrated_delay:.1f}s")
            return best_image
        cls._calibrated_delay = 3.0
        print(f"[PinkPawReward] 校准失败，默认延迟={cls._calibrated_delay}s")
        return img if (img is not None and isinstance(img, np.ndarray) and img.mean() >= 5) else None

    @classmethod
    def _capture_with_delay(cls, context: Context, delay: float):
        """使用已校准的延迟截图"""
        print(f"[PinkPawReward] 等待 {delay:.1f}s 后截图...")
        time.sleep(delay)
        image = context.tasker.controller.post_screencap().wait().get()
        if image is None or not isinstance(image, np.ndarray) or image.mean() < 5:
            time.sleep(2)
            image = context.tasker.controller.post_screencap().wait().get()
        return image

    @classmethod
    def _save_screenshot(cls, image, fansi: int, coins: int):
        """保存结算截图"""
        try:
            save_dir = cls._get_debug_dir()
            os.makedirs(save_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            run = cls._success_count + cls._fail_count
            path = os.path.join(save_dir, f"run{run}_f{fansi}_c{coins}_{ts}.png")
            if isinstance(image, np.ndarray):
                cv2.imwrite(path, image)
            print(f"[PinkPawReward] 截图已保存: {path}")
        except Exception as e:
            print(f"[PinkPawReward] 截图保存失败: {e}")

    @classmethod
    def ocr_rewards(cls, context: Context) -> tuple[int, int]:
        """
        截图并 OCR 识别结算界面的方斯和爪爪币。
        首局校准延迟，后续局复用。
        返回 (fansi, coins)。
        """
        if cls._calibrated_delay > 0:
            image = cls._capture_with_delay(context, cls._calibrated_delay)
        else:
            image = cls._calibrate_and_capture(context)

        if image is None or not isinstance(image, np.ndarray):
            print("[PinkPawReward] 无有效截图")
            return 0, 0

        # 方案1：标签+数值
        fansi = cls._ocr_labeled_value(context, image,
                                        cls.ROI_FANGS_LABEL, cls.ROI_FANGS_VALUE, "本局藏品价值")
        coins = cls._ocr_labeled_value(context, image,
                                        cls.ROI_COINS_LABEL, cls.ROI_COINS_VALUE, "爪爪币")
        # 方案2：整体 fallback
        if fansi is None and coins is None:
            fansi, coins = cls._ocr_panel_fallback(context, image)

        fansi = fansi or 0
        coins = coins or 0
        cls._save_screenshot(image, fansi, coins)
        return fansi, coins


def notify_pinkpaw_reward(context: Context, success: bool):
    """
    在 pinkpaw_core1/core2 中撤离后调用。
    success=True 时自动 OCR 识别收益。
    """
    if not PinkPawRewardTracker._initialized:
        PinkPawRewardTracker.reset()
        PinkPawRewardTracker._initialized = True

    if success:
        fansi, pinkcoins = PinkPawRewardTracker.ocr_rewards(context)
        PinkPawRewardTracker.on_evacuate_success(fansi, pinkcoins)
        msg = f"✅ 撤离成功！{PinkPawRewardTracker.get_msg()}"
        if fansi > 0 or pinkcoins > 0:
            msg += f"（本局 方斯+{fansi} 爪爪币+{pinkcoins}）"
    else:
        PinkPawRewardTracker.on_evacuate_fail()
        msg = f"❌ 撤离失败。{PinkPawRewardTracker.get_msg()}"

    print(f"[PinkPawReward] {msg}")
    try:
        context.override_pipeline({
            "PinkPawReward_Notify": {
                "recognition": "DirectHit",
                "action": "DoNothing",
                "focus": {"Node.Action.Starting": msg}
            }
        })
        context.run_task("PinkPawReward_Notify")
    except Exception:
        pass


@AgentServer.custom_action("pinkpaw_reward_summary")
class PinkPawRewardSummary(CustomAction):
    """任务完全结束时上报汇总"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        summary = PinkPawRewardTracker.get_summary()
        print(f"[PinkPawReward] {summary}")
        try:
            context.override_pipeline({
                "PinkPawReward_Summary": {
                    "recognition": "DirectHit",
                    "action": "DoNothing",
                    "focus": {"Node.Action.Starting": summary}
                }
            })
            context.run_task("PinkPawReward_Summary")
        except Exception:
            pass

        PinkPawRewardTracker.reset()
        PinkPawRewardTracker._initialized = False
        return CustomAction.RunResult(success=True)
