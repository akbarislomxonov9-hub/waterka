

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from config import MENU_INSTAGRAM, MENU_TIKTOK, MENU_MUSIC, MENU_GALLERY, MENU_SETTINGS


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_INSTAGRAM), KeyboardButton(text=MENU_TIKTOK)],
            [KeyboardButton(text=MENU_MUSIC), KeyboardButton(text=MENU_GALLERY)],
            [KeyboardButton(text=MENU_SETTINGS)],
        ],
        resize_keyboard=True,
    )
