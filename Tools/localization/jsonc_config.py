"""Read JSON with // comments, /* comments */ and trailing commas."""
import json
import re


def loads(text):
    output = []
    i = 0
    in_string = False
    while i < len(text):
        c = text[i]
        if in_string:
            output.append(c)
            i += 1
            if c == '\\' and i < len(text):
                output.append(text[i])
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            output.append(c)
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i + 2)
            end = len(text) if end < 0 else end
            output.append(' ' * (end - i))
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Unterminated JSONC block comment')
            output.append(''.join('\n' if ch == '\n' else ' ' for ch in text[i:end + 2]))
            i = end + 2
        else:
            output.append(c)
            i += 1
    # Match strings first so commas/comment-like text inside values stay intact.
    cleaned = re.sub(r'"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])',
                     lambda match: '' if match.group(0) == ',' else match.group(0),
                     ''.join(output))
    return json.loads(cleaned)
