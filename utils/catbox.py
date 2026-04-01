import aiohttp


async def upload_to_catbox(session: aiohttp.ClientSession, data: bytes, file_name: str) -> str | None:
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("userhash", "8fd8c70de12a94c7d910fca23")
    form.add_field("fileToUpload", data, filename=file_name)

    try:
        async with session.post("https://catbox.moe/user/api.php", data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = (await resp.text()).strip()
            if resp.status != 200:
                return None
            return text if text.startswith("http") else None
    except Exception:
        return None
