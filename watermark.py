

import os
import subprocess
import time

from yt_dlp import YoutubeDL

from config import (
    MAX_VIDEO_DURATION_SECONDS,
    MAX_DOWNLOAD_RETRIES,
    RETRY_BACKOFF_SECONDS,
    WATERMARK_WIDTH_RATIO,
    WATERMARK_HEIGHT_RATIO,
    logger,
)


def _download_video_once(url: str, out_dir: str) -> str:
    out_template = os.path.join(out_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "format": "mp4/best",
        "quiet": True,
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration = info.get("duration") or 0
        if duration and duration > MAX_VIDEO_DURATION_SECONDS:
            raise ValueError(
                f"Video juda uzun ({duration // 60} daqiqa). "
                f"Maksimal ruxsat etilgan: {MAX_VIDEO_DURATION_SECONDS // 60} daqiqa."
            )
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def download_video(url: str, out_dir: str) -> str:
    """
    yt-dlp yordamida videoni yuklaydi. Tarmoq xatosi bo'lsa
    MAX_DOWNLOAD_RETRIES marta qayta urinadi (backoff bilan).
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            return _download_video_once(url, out_dir)
        except ValueError:
            raise  # davomiylik chegarasi — qayta urinish shart emas
        except Exception as e:
            last_error = e
            logger.warning(
                "Yuklashda urinish %s/%s muvaffaqiyatsiz: %s",
                attempt, MAX_DOWNLOAD_RETRIES, e,
            )
            if attempt < MAX_DOWNLOAD_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    assert last_error is not None
    raise last_error


def get_video_resolution(filepath: str) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    width_str, height_str = result.stdout.strip().split("x")
    return int(width_str), int(height_str)


def remove_watermark(input_path: str, output_path: str, position: str, mode: str) -> None:
    """
    Videoning belgilangan burchagidagi username/watermarkni yo'q qiladi.

    mode == "box"  -> hududni to'liq qora to'rtburchak bilan qoplaydi
                       (100% kafolatlangan yashirish).
    mode == "blur" -> hududni kuchli boxblur bilan xiralashtiradi.
    """
    if position == "none":
        subprocess.run(["cp", input_path, output_path], check=True)
        return

    width, height = get_video_resolution(input_path)
    box_w = int(width * WATERMARK_WIDTH_RATIO)
    box_h = int(height * WATERMARK_HEIGHT_RATIO)

    positions = {
        "bottom_left": (0, height - box_h),
        "bottom_right": (width - box_w, height - box_h),
        "top_left": (0, 0),
        "top_right": (width - box_w, 0),
    }
    if position not in positions:
        raise ValueError(f"Noto'g'ri position: {position}")
    x, y = positions[position]

    if mode == "box":
        vf = f"drawbox=x={x}:y={y}:w={box_w}:h={box_h}:color=black@1.0:t=fill"
        cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    elif mode == "blur":
        filter_complex = (
            f"[0:v]split=2[base][blur_src];"
            f"[blur_src]crop={box_w}:{box_h}:{x}:{y},"
            f"boxblur=40:10:enable=1,boxblur=40:10:enable=1[blurred];"
            f"[base][blurred]overlay={x}:{y}[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?", "-c:a", "copy",
            output_path,
        ]
    else:
        raise ValueError(f"Noto'g'ri mode: {mode}")

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg xatoligi: {result.stderr.decode(errors='ignore')[:500]}")
