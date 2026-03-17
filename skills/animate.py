"""
Skill: animate
Creates a 15-second video animation of OHLC data moving left to right
while showing the 10 bullet points from bullets.json.
Uses the Arcade library for rendering and ffmpeg for video encoding.

Output: [company]/overview.mp4

Dependencies:
  - arcade (Python)
  - Pillow (Python)
  - ffmpeg (system)
  - Xvfb (system, for headless rendering)
"""
import os
import json
import subprocess
import shutil
from skills.base import BaseSkill
from utils import logger, ensure_company_dir
from claude_wrapper import log_to_runlog


class AnimateSkill(BaseSkill):
    name = "animate"
    description = "Create a 15s OHLC animation video with bullet points overlay"

    # Video settings
    WIDTH = 1280
    HEIGHT = 720
    FPS = 30
    DURATION = 15  # seconds
    TOTAL_FRAMES = FPS * DURATION  # 450

    def execute(self, ticker: str, company_name: str = None, **kwargs) -> dict:
        company_dir = ensure_company_dir(ticker)
        output_path = os.path.join(company_dir, "overview.mp4")

        log_to_runlog(f"Creating animation for {ticker}...")

        # Load required data
        bullets_path = os.path.join(company_dir, "bullets.json")
        ohlc_path = os.path.join(company_dir, "ohlc.json")
        logo_path = os.path.join(company_dir, "logo.jpeg")

        if not os.path.exists(bullets_path):
            return {"success": False, "error": "bullets.json not found - run ten-point-analysis first"}

        if not os.path.exists(ohlc_path):
            return {"success": False, "error": "ohlc.json not found - run analyze-price first"}

        with open(bullets_path, "r") as f:
            bullets = json.load(f)
        with open(ohlc_path, "r") as f:
            ohlc = json.load(f)

        has_logo = os.path.exists(logo_path) and os.path.getsize(logo_path) > 500

        # Create temp directory for frames
        frames_dir = os.path.join(company_dir, "_frames")
        os.makedirs(frames_dir, exist_ok=True)

        try:
            # Generate the animation script
            script_path = os.path.join(company_dir, "animate_script.py")
            self._write_animation_script(
                script_path, ticker, company_name or ticker,
                bullets, ohlc, logo_path if has_logo else None,
                frames_dir
            )

            # Ensure Xvfb is running
            self._ensure_xvfb()

            # Run the animation script
            log_to_runlog("Rendering animation frames...")
            env = os.environ.copy()
            env["DISPLAY"] = ":99"
            env["SDL_AUDIODRIVER"] = "dummy"

            result = subprocess.run(
                ["python3", script_path],
                capture_output=True, text=True, timeout=180,
                env=env, cwd=company_dir
            )

            if result.returncode != 0:
                stderr = result.stderr[-2000:] if result.stderr else "no stderr"
                stdout = result.stdout[-500:] if result.stdout else ""
                logger.error(f"Animation script failed: {stderr}")
                log_to_runlog(f"Animation script error: {stderr[:500]}")
                return {"success": False, "error": f"Animation script failed: {stderr[:500]}"}

            # Count frames
            frame_files = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
            if len(frame_files) < 10:
                return {"success": False, "error": f"Only {len(frame_files)} frames rendered"}

            log_to_runlog(f"Rendered {len(frame_files)} frames, encoding video...")

            # Encode to MP4 with ffmpeg
            ffmpeg_result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-framerate", str(self.FPS),
                    "-i", os.path.join(frames_dir, "frame_%05d.png"),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "medium",
                    "-crf", "23",
                    "-movflags", "+faststart",
                    output_path
                ],
                capture_output=True, text=True, timeout=120
            )

            if ffmpeg_result.returncode != 0:
                logger.error(f"ffmpeg failed: {ffmpeg_result.stderr[-500:]}")
                return {"success": False, "error": f"ffmpeg encoding failed"}

            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
                return {"success": False, "error": "Output video is too small or missing"}

            file_size = os.path.getsize(output_path)
            log_to_runlog(f"Animation complete: overview.mp4 ({file_size // 1024}KB)")

            return {
                "success": True,
                "video_path": output_path,
                "frame_count": len(frame_files),
                "file_size_kb": file_size // 1024,
            }

        finally:
            # Clean up frames directory
            if os.path.exists(frames_dir):
                shutil.rmtree(frames_dir, ignore_errors=True)

    def _ensure_xvfb(self):
        """Make sure Xvfb is running on :99."""
        check = subprocess.run(
            ["pgrep", "-f", "Xvfb :99"],
            capture_output=True, text=True
        )
        if check.returncode != 0:
            subprocess.Popen(
                ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            import time
            time.sleep(1)

    def _write_animation_script(self, script_path, ticker, company_name,
                                 bullets, ohlc, logo_path, frames_dir):
        """Write the Arcade animation script to a file."""

        # Prepare OHLC data - use inter_report window preferably
        ohlc_window = ohlc.get("inter_report", ohlc.get("post_earnings", {}))
        prices = []
        volumes = []
        dates = []
        for pt in ohlc_window.get("data", []):
            prices.append(float(pt.get("price", 0)))
            volumes.append(float(pt.get("volume", 0)))
            dates.append(pt.get("date", ""))

        # Prepare bullet points
        yay_list = []
        for b in bullets.get("yay", [])[:5]:
            yay_list.append({
                "title": str(b.get("title", "")),
                "detail": str(b.get("detail", "")),
                "metric": str(b.get("metric", "")),
            })
        nay_list = []
        for b in bullets.get("nay", [])[:5]:
            nay_list.append({
                "title": str(b.get("title", "")),
                "detail": str(b.get("detail", "")),
                "metric": str(b.get("metric", "")),
            })

        # Sentiment info
        sentiment = ohlc_window.get("sentiment", {})
        sentiment_label = sentiment.get("label", "Neutral")
        total_return = sentiment.get("total_return_pct", 0)

        # Escape strings for embedding in Python script
        def esc(s):
            return s.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34)).replace(chr(10), " ")

        script = f'''#!/usr/bin/env python3
"""Auto-generated OHLC animation script for {ticker}."""
import os
import sys
import math

# Suppress audio warnings
os.environ["SDL_AUDIODRIVER"] = "dummy"

import arcade
from arcade import LRBT

# ── Configuration ──
WIDTH = {self.WIDTH}
HEIGHT = {self.HEIGHT}
FPS = {self.FPS}
TOTAL_FRAMES = {self.TOTAL_FRAMES}
FRAMES_DIR = "{frames_dir}"
TICKER = "{esc(ticker)}"
COMPANY = "{esc(company_name)}"
LOGO_PATH = {f'"{logo_path}"' if logo_path else 'None'}

# ── Data ──
PRICES = {json.dumps(prices)}
VOLUMES = {json.dumps(volumes)}
DATES = {json.dumps(dates)}
YAY = {json.dumps(yay_list, ensure_ascii=False)}
NAY = {json.dumps(nay_list, ensure_ascii=False)}
SENTIMENT = "{esc(sentiment_label)}"
TOTAL_RETURN = {total_return}

# ── Colors ──
BG_COLOR = (15, 15, 35)
CHART_BG = (20, 25, 50, 255)
GRID_COLOR = (40, 50, 80, 255)
PRICE_LINE = (0, 200, 255, 255)
VOLUME_COLOR = (100, 120, 180, 100)
YAY_COLOR = (46, 204, 113, 255)
NAY_COLOR = (231, 76, 60, 255)
TEXT_WHITE = (240, 240, 255, 255)
TEXT_DIM = (140, 150, 180, 255)
ACCENT_GOLD = (255, 200, 60, 255)

# ── Chart area ──
CHART_LEFT = 60
CHART_RIGHT = WIDTH - 60
CHART_TOP = HEIGHT - 140
CHART_BOTTOM = 200
CHART_W = CHART_RIGHT - CHART_LEFT
CHART_H = CHART_TOP - CHART_BOTTOM

# ── Bullet display area ──
BULLET_Y_START = 170
BULLET_LINE_H = 30


class OHLCAnimation(arcade.Window):
    def __init__(self):
        super().__init__(WIDTH, HEIGHT, "OHLC Animation", visible=False)
        self.frame_num = 0
        self.set_update_rate(1 / FPS)

        # Precompute price range
        if PRICES:
            self.price_min = min(PRICES) * 0.98
            self.price_max = max(PRICES) * 1.02
        else:
            self.price_min, self.price_max = 0, 100

        if VOLUMES:
            self.vol_max = max(VOLUMES) * 1.2
        else:
            self.vol_max = 1

        self.n_points = len(PRICES)

        # Load logo
        self.logo_texture = None
        if LOGO_PATH and os.path.exists(LOGO_PATH):
            try:
                self.logo_texture = arcade.load_texture(LOGO_PATH)
            except Exception as e:
                print(f"Logo load failed: {{e}}")

        # Precompute bullet schedule
        self.bullet_schedule = []
        bullets_all = []
        for i, b in enumerate(YAY[:5]):
            bullets_all.append(("yay", i, b))
        for i, b in enumerate(NAY[:5]):
            bullets_all.append(("nay", i, b))

        for idx, (btype, bidx, bdata) in enumerate(bullets_all):
            start_frame = int(idx * TOTAL_FRAMES / 12)
            end_frame = min(start_frame + FPS * 4, TOTAL_FRAMES)
            self.bullet_schedule.append((btype, bidx, bdata, start_frame, end_frame))

    def on_update(self, delta_time):
        if self.frame_num >= TOTAL_FRAMES:
            arcade.close_window()
            return
        self.frame_num += 1

    def on_draw(self):
        if self.frame_num >= TOTAL_FRAMES:
            return

        self.clear(BG_COLOR)
        t = self.frame_num / TOTAL_FRAMES  # 0..1 progress

        # How many price points to show (progressive reveal)
        n_visible = max(2, int(t * self.n_points * 1.3))
        n_visible = min(n_visible, self.n_points)

        # ── Header ──
        self._draw_header(t)

        # ── Chart background ──
        arcade.draw_lrbt_rectangle_filled(
            CHART_LEFT - 5, CHART_RIGHT + 5,
            CHART_BOTTOM - 5, CHART_TOP + 5,
            CHART_BG
        )

        # ── Grid lines ──
        self._draw_grid()

        # ── Volume bars ──
        self._draw_volumes(n_visible)

        # ── Price line ──
        self._draw_price_line(n_visible)

        # ── Current price indicator ──
        if n_visible > 0 and n_visible <= self.n_points:
            self._draw_price_indicator(n_visible)

        # ── Bullet points ──
        self._draw_bullets()

        # ── Bottom bar ──
        arcade.draw_line(30, 25, WIDTH - 30, 25, GRID_COLOR, 1)
        arcade.draw_text(
            "Quarterly Earnings Research Report",
            WIDTH // 2, 10,
            (140, 150, 180, 120), font_size=9,
            anchor_x="center"
        )

        # ── Save frame ──
        img = arcade.get_image(0, 0, WIDTH, HEIGHT)
        frame_path = os.path.join(FRAMES_DIR, f"frame_{{self.frame_num:05d}}.png")
        img.save(frame_path)

        if self.frame_num % 90 == 0:
            print(f"Frame {{self.frame_num}}/{{TOTAL_FRAMES}}", flush=True)

    def _draw_header(self, t):
        """Draw the top header with logo, ticker, and sentiment."""
        y = HEIGHT - 40

        # Logo
        x_start = 30
        if self.logo_texture:
            logo_size = 60
            rect = LRBT(
                x_start, x_start + logo_size,
                y - 40, y + 20
            )
            arcade.draw_texture_rect(self.logo_texture, rect)
            x_start += logo_size + 15

        # Ticker and company name
        arcade.draw_text(
            TICKER, x_start, y,
            TEXT_WHITE, font_size=28, bold=True,
            anchor_y="top"
        )
        arcade.draw_text(
            COMPANY, x_start, y - 38,
            TEXT_DIM, font_size=13,
            anchor_y="top"
        )

        # Sentiment badge (right side)
        badge_x = WIDTH - 200
        if TOTAL_RETURN >= 0:
            badge_color = YAY_COLOR
            arrow = "+"
        else:
            badge_color = NAY_COLOR
            arrow = ""

        # Animate the return number
        shown_return = TOTAL_RETURN * min(t * 2, 1.0)
        arcade.draw_text(
            f"{{arrow}}{{shown_return:.1f}}%",
            badge_x, y - 5,
            badge_color, font_size=22, bold=True,
            anchor_y="top"
        )
        arcade.draw_text(
            SENTIMENT, badge_x, y - 35,
            TEXT_DIM, font_size=11,
            anchor_y="top"
        )

        # Date range
        if DATES:
            date_str = f"{{DATES[0]}}  ->  {{DATES[-1]}}"
            arcade.draw_text(
                date_str, WIDTH // 2, y - 55,
                TEXT_DIM, font_size=10,
                anchor_x="center", anchor_y="top"
            )

        # Separator line
        arcade.draw_line(30, HEIGHT - 110, WIDTH - 30, HEIGHT - 110, GRID_COLOR, 1)

    def _draw_grid(self):
        """Draw chart grid lines and price labels."""
        n_lines = 5
        for i in range(n_lines + 1):
            y = CHART_BOTTOM + (CHART_H * i / n_lines)
            arcade.draw_line(CHART_LEFT, y, CHART_RIGHT, y, GRID_COLOR, 1)
            price = self.price_min + (self.price_max - self.price_min) * i / n_lines
            arcade.draw_text(
                f"${{price:.0f}}", CHART_LEFT - 5, y,
                TEXT_DIM, font_size=8,
                anchor_x="right", anchor_y="center"
            )

    def _draw_volumes(self, n_visible):
        """Draw volume bars at the bottom of the chart."""
        if not VOLUMES or n_visible < 1:
            return
        bar_w = max(1, CHART_W / max(self.n_points, 1) * 0.6)
        vol_height = CHART_H * 0.15

        for i in range(n_visible):
            x = CHART_LEFT + (i / max(self.n_points - 1, 1)) * CHART_W
            vol_ratio = VOLUMES[i] / self.vol_max if self.vol_max > 0 else 0
            h = vol_ratio * vol_height
            if h > 0.5:
                arcade.draw_lrbt_rectangle_filled(
                    x - bar_w / 2, x + bar_w / 2,
                    CHART_BOTTOM, CHART_BOTTOM + h,
                    VOLUME_COLOR
                )

    def _draw_price_line(self, n_visible):
        """Draw the price line with fill."""
        if n_visible < 2:
            return

        points = []
        for i in range(n_visible):
            x = CHART_LEFT + (i / max(self.n_points - 1, 1)) * CHART_W
            price_ratio = (PRICES[i] - self.price_min) / max(self.price_max - self.price_min, 0.01)
            y = CHART_BOTTOM + price_ratio * CHART_H
            points.append((x, y))

        # Draw filled area under the line
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            mid_y = (y1 + y2) / 2
            alpha = int(40 * (mid_y - CHART_BOTTOM) / max(CHART_H, 1))
            alpha = max(5, min(60, alpha))
            fill_color = (0, 150, 255, alpha)
            arcade.draw_lrbt_rectangle_filled(
                x1, x2, CHART_BOTTOM, (y1 + y2) / 2,
                fill_color
            )

        # Draw the line itself
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            arcade.draw_line(x1, y1, x2, y2, PRICE_LINE, 2)

        # Glow effect on last point
        if points:
            lx, ly = points[-1]
            glow_alpha = int(80 + 40 * math.sin(self.frame_num * 0.15))
            arcade.draw_circle_filled(lx, ly, 6, (0, 200, 255, glow_alpha))
            arcade.draw_circle_filled(lx, ly, 3, PRICE_LINE)

    def _draw_price_indicator(self, n_visible):
        """Draw current price label at the chart edge."""
        idx = min(n_visible - 1, self.n_points - 1)
        price = PRICES[idx]
        price_ratio = (price - self.price_min) / max(self.price_max - self.price_min, 0.01)
        y = CHART_BOTTOM + price_ratio * CHART_H
        x = CHART_LEFT + (idx / max(self.n_points - 1, 1)) * CHART_W

        # Price tag
        arcade.draw_lrbt_rectangle_filled(
            CHART_RIGHT + 8, CHART_RIGHT + 75,
            y - 10, y + 10,
            PRICE_LINE
        )
        arcade.draw_text(
            f"${{price:.2f}}", CHART_RIGHT + 12, y,
            (255, 255, 255, 255), font_size=9, bold=True,
            anchor_y="center"
        )

        # Line from point to label
        arcade.draw_line(x, y, CHART_RIGHT + 8, y, (0, 200, 255, 80), 1)

        # Date label
        if idx < len(DATES) and DATES[idx]:
            arcade.draw_text(
                DATES[idx], x, CHART_BOTTOM - 15,
                TEXT_DIM, font_size=8,
                anchor_x="center", anchor_y="top"
            )

    def _draw_bullets(self):
        """Draw bullet points that appear and fade based on schedule."""
        active_yay = []
        active_nay = []

        for btype, bidx, bdata, start_f, end_f in self.bullet_schedule:
            if self.frame_num < start_f or self.frame_num > end_f:
                continue

            # Calculate opacity (fade in/out)
            fade_frames = FPS // 2
            if self.frame_num < start_f + fade_frames:
                alpha = (self.frame_num - start_f) / fade_frames
            elif self.frame_num > end_f - fade_frames:
                alpha = (end_f - self.frame_num) / fade_frames
            else:
                alpha = 1.0
            alpha = max(0.0, min(1.0, alpha))

            entry = (bidx, bdata, alpha)
            if btype == "yay":
                active_yay.append(entry)
            else:
                active_nay.append(entry)

        # Draw active yay bullets (left side)
        y = BULLET_Y_START
        for bidx, bdata, alpha in active_yay:
            a = int(alpha * 255)
            color = (46, 204, 113, a)
            white_color = (240, 240, 255, a)
            dim_color = (140, 150, 180, a)

            arcade.draw_text(
                "+", 40, y,
                color, font_size=12, bold=True,
                anchor_y="center"
            )
            title = bdata.get("title", "")[:40]
            arcade.draw_text(
                title, 60, y,
                white_color, font_size=11, bold=True,
                anchor_y="center"
            )
            metric = bdata.get("metric", "")[:35]
            arcade.draw_text(
                metric, 60, y - 16,
                dim_color, font_size=9,
                anchor_y="center"
            )
            y -= BULLET_LINE_H + 8

        # Draw active nay bullets (right side)
        y = BULLET_Y_START
        for bidx, bdata, alpha in active_nay:
            a = int(alpha * 255)
            color = (231, 76, 60, a)
            white_color = (240, 240, 255, a)
            dim_color = (140, 150, 180, a)

            arcade.draw_text(
                "-", WIDTH // 2 + 40, y,
                color, font_size=12, bold=True,
                anchor_y="center"
            )
            title = bdata.get("title", "")[:40]
            arcade.draw_text(
                title, WIDTH // 2 + 60, y,
                white_color, font_size=11, bold=True,
                anchor_y="center"
            )
            metric = bdata.get("metric", "")[:35]
            arcade.draw_text(
                metric, WIDTH // 2 + 60, y - 16,
                dim_color, font_size=9,
                anchor_y="center"
            )
            y -= BULLET_LINE_H + 8


def main():
    os.makedirs(FRAMES_DIR, exist_ok=True)
    print(f"Rendering {{TOTAL_FRAMES}} frames at {{FPS}}fps ({{TOTAL_FRAMES/FPS:.0f}}s)...")
    print(f"Data: {{len(PRICES)}} price points")

    window = OHLCAnimation()
    arcade.run()
    print(f"Done! Frames saved to {{FRAMES_DIR}}")


if __name__ == "__main__":
    main()
'''

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        logger.info(f"Animation script written to {script_path}")