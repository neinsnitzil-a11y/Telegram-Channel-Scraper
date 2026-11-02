"""Extract every literal URL from text/channel exports without visiting remote sites."""
from pathlib import Path
from link_downloader import extract_links


def urls_from_text(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    msg = type('TextMessage', (), {'raw_text': text, 'get_entities_text': None,
                                    'buttons': None, 'reply_markup': None})()
    return extract_links(msg)


def write_txt(path, lines):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(('\n'.join(lines) + '\n') if lines else '', encoding='utf-8')
    temporary.replace(target)
