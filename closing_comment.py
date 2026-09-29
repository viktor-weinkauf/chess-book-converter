"""Bound post-result commentary without playing or repairing its moves."""
import re


CONTEXT = re.compile(r'\b(?:анализ\w*|вариант\w*|позици\w*|analysis|variation|position)\b', re.I)
LEAD = re.compile(r'^(?:на|после|если|ввиду|так как|if|after|because)\b', re.I)
MOVE = re.compile(r'^(?:[KК][pр]|[KQRBNКФЛС♔-♟])?[a-hасе]?[x:]?[a-hасе][1-8]', re.I)
ASSESSMENT = re.compile(r'^(?:(?:чистая|убедительная|красивая)\s+победа[.!]?|\d{1,2}:\d{1,2}[.!]?)$', re.I)


def bounded_tail(tokens, index):
    """Return a nearby chess paragraph and its exclusive end, or a reason.

    An explicit paragraph/title/credit boundary is required. Limits are guards,
    never invented paragraph boundaries. Ambiguous text stays out of the PGN.
    """
    page, line = tokens[index][2:]
    start = index + 1
    # A result printed on its own line may have a blank line before the note.
    if start < len(tokens) and tokens[start][0] == 'paragraph':
        start += 1
    end = None
    for k in range(start, min(start + 160, len(tokens))):
        kind, value, p, l = tokens[k]
        if p != page or l - line > 12:
            break
        if kind in {'paragraph', 'game_boundary', 'game_end'}:
            end = k
            break
        if kind in {'game_result', 'fen', 'board', 'diagram'}:
            break
    if end is None:
        return None, 'no_nearby_boundary'
    span = tokens[start:end]
    if not span:
        return None, 'empty'
    intro = []
    depth = 0
    numbers = moves = 0
    pieces = []
    for kind, value, p, l in span:
        if kind == 'num':
            numbers += 1
            pieces.append(f'{value[0]}{"..." if value[1] else "."}')
        elif kind == 'word':
            if not numbers:
                intro.append(value)
            moves += bool(MOVE.match(value))
            pieces.append(value)
        elif kind in {'open', 'close'}:
            depth += 1 if kind == 'open' else -1
            if depth < 0:
                return None, 'unbalanced_brackets'
            pieces.append('(' if kind == 'open' else ')')
        else:
            return None, 'unsupported_structure'
    if depth:
        return None, 'unbalanced_brackets'
    lead = ' '.join(intro[:18])
    if not (CONTEXT.search(lead) or numbers and LEAD.search(lead)
            or not numbers and ASSESSMENT.fullmatch(' '.join(pieces))):
        return None, 'no_chess_introduction'
    if numbers and moves < 2:
        return None, 'insufficient_notation'
    if not numbers and len(span) > 25:
        return None, 'unbounded_prose_subject'
    return dict(text=' '.join(pieces), start=start, end=end, page=page,
                first_line=span[0][3], last_line=span[-1][3], boundary=tokens[end][0]), None
