"""Mark the changes between two EPLAN schema revisions in one PDF.

Usage:  python eplan_diff.py OLD.pdf NEW.pdf [-o OUT.pdf] [--report report.txt]

Old sheet with the old value in pink, new sheet with the new value in green,
content that only flowed to another sheet stays unmarked. Ends with a completeness
check that fails loudly when a difference is not visible in the result.
"""

import argparse
import difflib
import re
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import pymupdf

DPI = 200
SCALE = DPI / 72.0
CELL = 24
CLUSTER_REACH = 3
PAD = 1.0
MERGE_GAP = 1.0
TEXT_KEEPOUT = 3.0
ROW_TOLERANCE = 3.0
ARTICLE_GAP = 6.0
OVERFLOW_SHARE = 0.4
ARTICLE_ALIGN = 1.0
OVERLAP_RATIO = 0.5
GRAPHICS_AREA_LIMIT = 0.20
MAX_GRAPHICS_CLUSTERS = 6
GRAPHICS_TEXT_REACH = 6.0
GRAPHICS_TEXT_ROUNDS = 5
SHIFT_SEARCH = 220
SHIFT_COVERAGE = 0.9
SHIFT_COVERAGE_AT_SHIFTED_TEXT = 0.5
TEXT_OFFSET_CANDIDATES = 12

MARK_OLD = (0.97, 0.70, 0.92)
MARK_NEW = (0.70, 0.98, 0.70)

# Title block fields as a fraction of the sheet, so other sheet sizes work too.
FIELD_FUNCTION = (0.9130, 0.9471, 0.9633, 0.9726)
FIELD_NUMBER = (0.9633, 0.9471, 1.0000, 0.9726)
FIELD_DESCRIPTION = (0.3518, 0.9448, 0.5654, 0.9981)
FIELD_SHEET_CELL = (0.9649, 0.9459, 0.9976, 0.9749)
# Editor, dates and revision change on every sheet with each release - not a change a fitter acts on.
FIELD_TITLE_BLOCK = (0.0, 0.9330, 1.0, 1.0)


def field(page, fractions):
    x0, y0, x1, y1 = fractions
    rect = pymupdf.Rect(page.rect.x0 + x0 * page.rect.width, page.rect.y0 + y0 * page.rect.height,
                        page.rect.x0 + x1 * page.rect.width, page.rect.y0 + y1 * page.rect.height)
    return rect


def field_text(page, fractions):
    return ' '.join(page.get_text(clip=field(page, fractions)).split())


def sheet_key(page):
    key = '%s|%s' % (field_text(page, FIELD_FUNCTION), field_text(page, FIELD_NUMBER))
    return key if key.strip('|') else ' '.join(page.get_text().split())[:80]


def sheet_label(page):
    lines = [l.strip() for l in page.get_text(clip=field(page, FIELD_DESCRIPTION)).split('\n') if l.strip()]
    function = field_text(page, FIELD_FUNCTION)
    return ('%s / %s' % (function or 'Blatt', field_text(page, FIELD_NUMBER))).strip(), ': '.join(lines[0:3:2])


def align_sheets(old, new):
    """Match every old sheet to the new sheet carrying the same designation."""
    keys_old = [sheet_key(page) for page in old]
    keys_new = [sheet_key(page) for page in new]
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, keys_old, keys_new, autojunk=False).get_opcodes():
        if tag == 'equal':
            pairs += list(zip(range(i1, i2), range(j1, j2)))
            continue
        left, right = list(range(i1, i2)), list(range(j1, j2))
        for index in range(max(len(left), len(right))):
            pairs.append((left[index] if index < len(left) else None,
                          right[index] if index < len(right) else None))
    return pairs


def title_block(page):
    """Only landscape sheets carry the EPLAN title block; portrait inserts are plain content."""
    return field(page, FIELD_TITLE_BLOCK) if page.rect.width > page.rect.height else None


def in_title_block(page, rect):
    block = title_block(page)
    return block is not None and block.contains((rect.tl + rect.br) / 2)


def text_blocks(page, include_title_block=False):
    result = []
    for block in page.get_text('dict')['blocks']:
        items = [(span['text'].strip(), pymupdf.Rect(span['bbox']))
                 for line in block.get('lines', []) for span in line['spans'] if span['text'].strip()]
        if not include_title_block:
            items = [item for item in items if not in_title_block(page, item[1])]
        if items:
            items.sort(key=lambda item: (round(item[1].y0 / 2), round(item[1].x0, 1)))
            result.append(items)
    return result


def text_rows(page, include_title_block=False):
    """Table-like rows inside each text block - the unit a technician reads as one entry."""
    rows = []
    for items in text_blocks(page, include_title_block):
        current, top = [], None
        for text, rect in items:
            if top is None or rect.y0 - top > ROW_TOLERANCE:
                if current:
                    rows.append(current)
                current, top = [], rect.y0
            current.append((text, rect))
        if current:
            rows.append(current)
    for row in rows:
        row.sort(key=lambda item: round(item[1].x0, 1))
    return [(' '.join(t for t, _ in row), row) for row in rows]


def document_rows(doc):
    return [(index, token, items) for index, page in enumerate(doc) for token, items in text_rows(page)]


def row_rect(items):
    rect = pymupdf.Rect(items[0][1])
    for _, other in items[1:]:
        rect |= other
    return rect


def span_diff(items_old, items_new):
    matcher = difflib.SequenceMatcher(None, [t for t, _ in items_old], [t for t, _ in items_new], autojunk=False)
    changed_old, changed_new = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('delete', 'replace'):
            changed_old += items_old[i1:i2]
        if tag in ('insert', 'replace'):
            changed_new += items_new[j1:j2]
    return [r for _, r in changed_old], [r for _, r in changed_new]


def page_slices(sequence):
    slices = {}
    for position, (page_index, _, _) in enumerate(sequence):
        start, _ = slices.get(page_index, (position, position))
        slices[page_index] = (start, position + 1)
    return slices


def flagged_positions(old, new, page_map):
    """Compare every sheet against the sheet it was matched with, never the document as a whole.

    A globally aligned row diff drifts across sheets as soon as one sheet is inserted.
    """
    slices_old, slices_new = page_slices(old), page_slices(new)
    in_old, in_new = [], []
    for page_old, page_new in page_map.items():
        start_old, end_old = slices_old.get(page_old, (0, 0))
        start_new, end_new = slices_new.get(page_new, (0, 0))
        matcher = difflib.SequenceMatcher(None, [t for _, t, _ in old[start_old:end_old]],
                                          [t for _, t, _ in new[start_new:end_new]], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            in_old += range(start_old + i1, start_old + i2)
            in_new += range(start_new + j1, start_new + j2)
    return in_old, in_new


def neighbourhood(page_index, page_map):
    """The matched sheet of the other version and its direct neighbours."""
    mapped = page_map.get(page_index)
    if mapped is not None:
        return {mapped - 1, mapped, mapped + 1}
    return {page_map[p] + offset for p in (page_index - 1, page_index + 1) if p in page_map for offset in (-1, 0, 1)}


def repeated_nearby(rows, other_rows, page_map):
    """Test whether a row's text stands on the matched sheet of the other version or next to it.

    A group heading is repeated on every sheet the group runs over. When content grows, a group
    breaks at another place and its heading turns up on a sheet where it never stood - without
    leaving its place anywhere else.
    """
    pages_by_token = defaultdict(set)
    for page_index, token, _ in other_rows:
        pages_by_token[token].add(page_index)

    def test(position):
        page_index, token, _ = rows[position]
        return bool(pages_by_token.get(token, set()) & neighbourhood(page_index, page_map))
    return test


def overflow_sheets(rows, flagged_other, paired_pages, is_repeated):
    """Unpaired sheets carrying only rows that left their place in the other version or repeat a heading.

    Such a sheet exists because content before it grew and pushed the rest onward - it holds
    nothing new, so it is a shift target like any paired sheet and not part of the diff.
    """
    tokens = defaultdict(Counter)
    positions = defaultdict(dict)
    for position, (page_index, token, _) in enumerate(rows):
        tokens[page_index][token] += 1
        positions[page_index].setdefault(token, position)
    return {page_index for page_index, page_tokens in tokens.items()
            if page_index not in paired_pages
            and all(is_repeated(positions[page_index][token]) for token in page_tokens - flagged_other)}


def overflow_with_additions(rows, flagged_other, paired_pages, full_overflow):
    """Unpaired sheets that mostly hold content pushed on from the sheet before, plus a few new rows.

    Measured by the characters of rows that left their place in the other version - short generic
    labels (`/`, `SH`, `1`) stand on every schematic and would make a brand-new sheet look pushed on.
    """
    total, pushed = Counter(), Counter()
    for page_index, token, _ in rows:
        if page_index in paired_pages or page_index in full_overflow:
            continue
        total[page_index] += len(token)
        if flagged_other[token]:
            pushed[page_index] += len(token)
    return {page_index for page_index, chars in total.items() if pushed[page_index] >= OVERFLOW_SHARE * chars}


def match_moves(old, new, candidates_old, candidates_new, page_map):
    """A row moved only when the same text left its place in the other version.

    Text that stands unchanged elsewhere does not count - a new article carrying a common
    part number or manufacturer is still new. Every occurrence is used once, nearest sheet first.
    """
    by_token_new = defaultdict(list)
    for position in candidates_new:
        by_token_new[new[position][1]].append(position)
    options = []
    for position in candidates_old:
        page_old = old[position][0]
        for candidate in by_token_new.get(old[position][1], ()):
            options.append((abs(page_map.get(page_old, page_old) - new[candidate][0]), position, candidate))
    options.sort()
    moved_old, moved_new = set(), set()
    for _, position, candidate in options:
        if position not in moved_old and candidate not in moved_new:
            moved_old.add(position)
            moved_new.add(candidate)
    return moved_old, moved_new


def pair_in_place(old, new, in_old, in_new, page_map):
    """Rows on the same spot of the matched sheet - the value there was replaced."""
    by_page_new = defaultdict(list)
    for position in in_new:
        by_page_new[new[position][0]].append(position)
    pairs, taken = [], set()
    for position in in_old:
        rect_old = row_rect(old[position][2])
        for candidate in by_page_new.get(page_map.get(old[position][0]), ()):
            if candidate in taken:
                continue
            rect_new = row_rect(new[candidate][2])
            overlap = (rect_old & rect_new).get_area()
            if overlap and overlap / min(rect_old.get_area(), rect_new.get_area()) >= OVERLAP_RATIO:
                taken.add(candidate)
                pairs.append((position, candidate))
                break
    paired_old = {position for position, _ in pairs}
    return pairs, [p for p in in_old if p not in paired_old], [p for p in in_new if p not in taken]


def same_article(a, b):
    """Label lines of one device stack flush left or right with hardly any gap."""
    gap = max(a.y0, b.y0) - min(a.y1, b.y1)
    aligned = abs(a.x0 - b.x0) <= ARTICLE_ALIGN or abs(a.x1 - b.x1) <= ARTICLE_ALIGN
    return gap <= ARTICLE_GAP and aligned


def article_completion(rows, marked, flagged):
    """Flagged rows that belong to an article in which something is marked.

    Parts of an article - length, shield, manufacturer - often also changed on another sheet
    and are then taken for shifted. The technician needs the whole article.
    """
    by_page = defaultdict(list)
    for position in flagged:
        by_page[rows[position][0]].append(position)
    added = set()
    for positions in by_page.values():
        if not any(position in marked for position in positions):
            continue
        rects = {position: row_rect(rows[position][2]) for position in positions}
        group = {position: position for position in positions}

        def root(position):
            while group[position] != position:
                group[position] = group[group[position]]
                position = group[position]
            return position

        for index, a in enumerate(positions):
            for b in positions[index + 1:]:
                if same_article(rects[a], rects[b]):
                    group[root(a)] = root(b)
        marked_groups = {root(position) for position in positions if position in marked}
        added |= {position for position in positions if position not in marked and root(position) in marked_groups}
    return added


def text_diff(doc_old, doc_new, page_map):
    old, new = document_rows(doc_old), document_rows(doc_new)
    reverse_map = {value: key for key, value in page_map.items()}
    in_old, in_new = flagged_positions(old, new, page_map)
    repeated_old = repeated_nearby(old, new, page_map)
    repeated_new = repeated_nearby(new, old, reverse_map)
    flagged_old, flagged_new = Counter(old[p][1] for p in in_old), Counter(new[p][1] for p in in_new)
    overflow_old = overflow_sheets(old, flagged_new, set(page_map), repeated_old)
    overflow_new = overflow_sheets(new, flagged_old, set(page_map.values()), repeated_new)
    extended_old = overflow_with_additions(old, flagged_new, set(page_map), overflow_old)
    extended_new = overflow_with_additions(new, flagged_old, set(page_map.values()), overflow_new)
    unpaired_old = [p for p, row in enumerate(old) if row[0] in extended_old]
    unpaired_new = [p for p, row in enumerate(new) if row[0] in extended_new]
    candidates_old = in_old + unpaired_old + [p for p, row in enumerate(old) if row[0] in overflow_old]
    candidates_new = in_new + unpaired_new + [p for p, row in enumerate(new) if row[0] in overflow_new]
    moved_old, moved_new = match_moves(old, new, candidates_old, candidates_new, page_map)

    pairs, rest_old, rest_new = pair_in_place(old, new, [p for p in in_old if p not in moved_old],
                                              [p for p in in_new if p not in moved_new], page_map)
    rest_old += [p for p in unpaired_old if p not in moved_old]
    rest_new += [p for p in unpaired_new if p not in moved_new]
    in_old, in_new = in_old + unpaired_old, in_new + unpaired_new
    moved_old |= {p for p in rest_old if repeated_old(p)}
    moved_new |= {p for p in rest_new if repeated_new(p)}
    rest_old = [p for p in rest_old if p not in moved_old]
    rest_new = [p for p in rest_new if p not in moved_new]
    rects_old, rects_new = defaultdict(list), defaultdict(list)
    for position_old, position_new in pairs:
        changed_old, changed_new = span_diff(old[position_old][2], new[position_new][2])
        rects_old[old[position_old][0]] += changed_old
        rects_new[new[position_new][0]] += changed_new

    marked_old = set(rest_old) | {p for p, _ in pairs}
    marked_new = set(rest_new) | {p for _, p in pairs}
    whole_old = set(rest_old) | article_completion(old, marked_old, in_old)
    whole_new = set(rest_new) | article_completion(new, marked_new, in_new)
    for position in whole_old:
        rects_old[old[position][0]] += [rect for _, rect in old[position][2]]
    for position in whole_new:
        rects_new[new[position][0]] += [rect for _, rect in new[position][2]]

    shifted_old, shifted_new = defaultdict(list), defaultdict(list)
    shifted = []
    for position in sorted(moved_old - whole_old):
        shifted_old[old[position][0]].append(row_rect(old[position][2]))
        shifted.append(('ALT', old[position][0] + 1, old[position][1]))
    for position in sorted(moved_new - whole_new):
        shifted_new[new[position][0]].append(row_rect(new[position][2]))
        shifted.append(('NEU', new[position][0] + 1, new[position][1]))

    added_spans = defaultdict(list)
    for position in whole_new | marked_new:
        added_spans[new[position][0]] += [rect for _, rect in new[position][2]]

    return dict(rects_old), dict(rects_new), dict(shifted_old), dict(shifted_new), dict(added_spans), shifted, \
        overflow_old, overflow_new, extended_old, extended_new


def gray(page):
    pixmap = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)


def dilate(mask, radius=1):
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


def text_mask(page, shape):
    mask = np.zeros(shape, bool)
    block = title_block(page)
    if block is not None:
        mask[max(0, int(block.y0 * SCALE)):, :] = True
    for _, items in text_rows(page, include_title_block=True):
        for _, rect in items:
            x0 = max(0, int((rect.x0 - TEXT_KEEPOUT) * SCALE))
            y0 = max(0, int((rect.y0 - TEXT_KEEPOUT) * SCALE))
            x1 = min(shape[1], int((rect.x1 + TEXT_KEEPOUT) * SCALE) + 1)
            y1 = min(shape[0], int((rect.y1 + TEXT_KEEPOUT) * SCALE) + 1)
            mask[y0:y1, x0:x1] = True
    return mask


def clusters(mask):
    h, w = mask.shape
    gh, gw = -(-h // CELL), -(-w // CELL)
    padded = np.zeros((gh * CELL, gw * CELL), bool)
    padded[:h, :w] = mask
    grid = padded.reshape(gh, CELL, gw, CELL).any(axis=(1, 3))
    seen = np.zeros_like(grid)
    boxes = []
    for y, x in zip(*np.nonzero(grid)):
        if seen[y, x]:
            continue
        stack, cells = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny in range(max(0, cy - CLUSTER_REACH), min(gh, cy + CLUSTER_REACH + 1)):
                for nx in range(max(0, cx - CLUSTER_REACH), min(gw, cx + CLUSTER_REACH + 1)):
                    if grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys = [c[0] for c in cells]
        xs = [c[1] for c in cells]
        boxes.append(pymupdf.Rect(min(xs) * CELL / SCALE, min(ys) * CELL / SCALE,
                                  (max(xs) + 1) * CELL / SCALE, (max(ys) + 1) * CELL / SCALE))
    return boxes


def shift_coverage(source, other, box, text_offsets=()):
    """How much of the box's ink sits elsewhere on the other sheet - 1.0 means the drawing only moved.

    Searched are small vertical shifts plus every offset by which the sheet's text moved:
    frames travel with their entries, also further away and into the other column.
    """
    x0, y0 = int(box.x0 * SCALE), int(box.y0 * SCALE)
    x1, y1 = int(box.x1 * SCALE), int(box.y1 * SCALE)
    patch = source[y0:y1, x0:x1]
    ink = patch.sum()
    if ink == 0:
        return 1.0
    offsets = [(0, dy) for dy in range(-SHIFT_SEARCH, SHIFT_SEARCH + 1)]
    offsets += [(int(dx * SCALE), int(dy * SCALE)) for dx, dy in text_offsets]
    best = 0.0
    for dx, dy in offsets:
        if y0 + dy < 0 or y1 + dy > other.shape[0] or x0 + dx < 0 or x1 + dx > other.shape[1]:
            continue
        best = max(best, (patch & other[y0 + dy:y1 + dy, x0 + dx:x1 + dx]).sum() / ink)
        if best >= SHIFT_COVERAGE:
            break
    return best


def grow_to_text(spans, boxes):
    """A changed drawing carries its label - take the whole neighbouring text with it.

    Only text the sheet comparison saw as added may be pulled in. Text standing unchanged
    next to the drawing was there before, even when it touches the box.
    """
    grown = []
    for box in boxes:
        result = pymupdf.Rect(box)
        for _ in range(GRAPHICS_TEXT_ROUNDS):
            reach = result + (-GRAPHICS_TEXT_REACH, -GRAPHICS_TEXT_REACH,
                              GRAPHICS_TEXT_REACH, GRAPHICS_TEXT_REACH)
            widened = pymupdf.Rect(result)
            for rect in spans:
                if reach.intersects(rect):
                    widened |= rect
            if widened == result:
                break
            result = widened
        grown.append(result)
    return grown


def is_moved_graphic(source, other, box, text_offsets, shifted, marked):
    """Ink found elsewhere, or frames and backgrounds that travelled with their shifted rows.

    A box at shifted text often also holds one new piece - a repeated table head, say - and then
    misses the full coverage; half of it found elsewhere is enough there. A removed or added
    device box is found nowhere and stays, also when shifted pin labels sit inside it.
    """
    coverage = shift_coverage(source, other, box, text_offsets)
    if coverage >= SHIFT_COVERAGE:
        return True
    touches_marked = any(box.intersects(r) for r in marked)
    reach = box + (-GRAPHICS_TEXT_REACH, -GRAPHICS_TEXT_REACH, GRAPHICS_TEXT_REACH, GRAPHICS_TEXT_REACH)
    if any(reach.intersects(r) for r in shifted):
        return coverage >= SHIFT_COVERAGE_AT_SHIFTED_TEXT or not touches_marked
    # Frames and heading bars run across the whole table row, far beyond the text they carry.
    in_row = any(reach.y0 <= r.y1 and r.y0 <= reach.y1 for r in shifted)
    return in_row and coverage >= SHIFT_COVERAGE_AT_SHIFTED_TEXT and not touches_marked


def text_movement(page_old, page_new):
    """How far the rows that stand once on both sheets moved, and where those rows stand.

    Unchanged text that only changed its place carries its frame along just like shifted text.
    """
    def unique_rows(page):
        rows = defaultdict(list)
        for token, items in text_rows(page):
            rows[token].append(row_rect(items))
        return {token: rects[0] for token, rects in rows.items() if len(rects) == 1}

    rows_old, rows_new = unique_rows(page_old), unique_rows(page_new)
    offsets = Counter()
    relocated_old, relocated_new = [], []
    for token, rect in rows_old.items():
        if token not in rows_new:
            continue
        offset = (round(rows_new[token].x0 - rect.x0), round(rows_new[token].y0 - rect.y0))
        offsets[offset] += 1
        if offset != (0, 0):
            relocated_old.append(rect)
            relocated_new.append(rows_new[token])
    forward = [offset for offset, _ in offsets.most_common(TEXT_OFFSET_CANDIDATES) if offset != (0, 0)]
    return forward, relocated_old, relocated_new


def graphics_diff(page_old, page_new, added_spans=(), shifted=((), ()), marked=((), ())):
    go, gn = gray(page_old), gray(page_new)
    if go.shape != gn.shape:
        h, w = min(go.shape[0], gn.shape[0]), min(go.shape[1], gn.shape[1])
        go, gn = go[:h, :w], gn[:h, :w]
    shape = go.shape
    mask_old, mask_new = text_mask(page_old, shape), text_mask(page_new, shape)
    outside_text = ~(mask_old | mask_new)
    ink_old, ink_new = (go < 220) & outside_text, (gn < 220) & outside_text
    wide_old, wide_new = dilate(ink_old), dilate(ink_new)
    # The shift search needs each sheet cut by its own text only: the other sheet's text sits
    # one table row away after an insertion and would cut exactly the lines that moved.
    shift_old, shift_new = dilate((go < 220) & ~mask_old), dilate((gn < 220) & ~mask_new)
    forward, relocated_old, relocated_new = text_movement(page_old, page_new)
    backward = [(-dx, -dy) for dx, dy in forward]
    moving_old, moving_new = list(shifted[0]) + relocated_old, list(shifted[1]) + relocated_new
    boxes_old = [b for b in clusters(ink_old & ~wide_new)
                 if not is_moved_graphic(ink_old, shift_new, b, forward, moving_old, marked[0])]
    boxes_new = [b for b in clusters(ink_new & ~wide_old)
                 if not is_moved_graphic(ink_new, shift_old, b, backward, moving_new, marked[1])]
    limit = shape[0] * shape[1] * GRAPHICS_AREA_LIMIT / (SCALE * SCALE)
    dropped = []
    if len(boxes_old) > MAX_GRAPHICS_CLUSTERS or sum(b.get_area() for b in boxes_old) > limit:
        dropped += [('ALT', b) for b in boxes_old]
        boxes_old = []
    if len(boxes_new) > MAX_GRAPHICS_CLUSTERS or sum(b.get_area() for b in boxes_new) > limit:
        dropped += [('NEU', b) for b in boxes_new]
        boxes_new = []
    return boxes_old, grow_to_text(list(added_spans), boxes_new), dropped


def merge_rects(rects):
    boxes = [pymupdf.Rect(r) + (-PAD, -PAD, PAD, PAD) for r in rects]
    changed = True
    while changed:
        changed = False
        merged = []
        for box in boxes:
            for index, other in enumerate(merged):
                if (box + (-MERGE_GAP, -MERGE_GAP, MERGE_GAP, MERGE_GAP)).intersects(other):
                    merged[index] = other | box
                    changed = True
                    break
            else:
                merged.append(box)
        boxes = merged
    return boxes


def collect_changes(old, new, pairs, log):
    page_map = {oi: nj for oi, nj in pairs if oi is not None and nj is not None}
    log('Zeilenvergleich ...')
    text_old, text_new, shifted_old, shifted_new, added_spans, shifted, overflow_old, overflow_new, \
        extended_old, extended_new = text_diff(old, new, page_map)
    for page_index in sorted(overflow_old):
        log('  ALT Blatt %d entfallen, Inhalt nur umgebrochen - nicht aufgenommen' % (page_index + 1))
    for page_index in sorted(overflow_new):
        log('  NEU Blatt %d hinzugekommen, Inhalt nur umgebrochen - nicht aufgenommen' % (page_index + 1))
    for page_index in sorted(extended_old):
        log('  ALT Blatt %d entfallen, Inhalt ueberwiegend umgebrochen - nur Entferntes markiert' % (page_index + 1))
    for page_index in sorted(extended_new):
        log('  NEU Blatt %d hinzugekommen, Inhalt ueberwiegend umgebrochen - nur Neues markiert' % (page_index + 1))
    changes, dropped_graphics = [], []
    for number, (oi, nj) in enumerate(pairs):
        if oi in overflow_old or nj in overflow_new:
            continue
        if oi in extended_old or nj in extended_new:
            rects_old, rects_new = text_old.get(oi, []), text_new.get(nj, [])
            if rects_old or rects_new:
                changes.append((oi, nj, merge_rects(rects_old), merge_rects(rects_new), False))
            continue
        if oi is None or nj is None:
            page = new[nj] if nj is not None else old[oi]
            cell = field(page, FIELD_SHEET_CELL)
            changes.append((oi, nj, [cell] if oi is not None else [], [cell] if nj is not None else [], True))
            continue
        rects_old = list(text_old.get(oi, []))
        rects_new = list(text_new.get(nj, []))
        boxes_old, boxes_new, dropped = graphics_diff(
            old[oi], new[nj], added_spans.get(nj, ()),
            (shifted_old.get(oi, ()), shifted_new.get(nj, ())), (rects_old, rects_new))
        rects_old += boxes_old
        rects_new += boxes_new
        dropped_graphics += [(oi + 1, nj + 1, side, box) for side, box in dropped]
        if rects_old or rects_new:
            changes.append((oi, nj, merge_rects(rects_old), merge_rects(rects_new), False))
        if number % 25 == 0:
            log('  Grafikvergleich %d/%d' % (number, len(pairs)))
    return changes, shifted, dropped_graphics


def mark(page, boxes, color, badge, note):
    for box in boxes:
        annot = page.add_rect_annot(box & page.rect)
        annot.set_colors(stroke=color, fill=color)
        annot.set_border(width=0)
        annot.set_blendmode(pymupdf.PDF_BM_Multiply)
        annot.set_info(title='Schema-Vergleich', content=note)
        annot.update(fill_color=color)
    width = pymupdf.get_text_length(badge, fontname='hebo', fontsize=10) + 16
    page.draw_rect(pymupdf.Rect(6, 2, 6 + width, 22), color=(0.25, 0.25, 0.25), fill=color, width=0.8)
    page.insert_text((14, 16), badge, fontsize=10, fontname='hebo', color=(0, 0, 0))


def revision_of(name):
    """Revision index out of a file name like 1045-0583_EPLAN_01_EN_DE.pdf."""
    match = re.search(r'[_-](\d{2})(?=[_.])', name)
    return '  -  Rev. %s' % match.group(1) if match else ''


def badge_text(side, revision, sheet_number, count, whole_sheet, unpaired=False):
    if whole_sheet:
        return '%s%s  -  Blatt %d  -  Blatt %s' % (
            side, revision, sheet_number, 'komplett neu' if side == 'NEU' else 'komplett entfallen')
    reason = ('neu durch Umbruch  -  ' if side == 'NEU' else 'entfallen durch Umbruch  -  ') if unpaired else ''
    return '%s%s  -  Blatt %d  -  %s%d markierte Stelle%s' % (
        side, revision, sheet_number, reason, count, '' if count == 1 else 'n')


def build_body(changes, old, new, revisions):
    body = pymupdf.open()
    rows = []
    for oi, nj, boxes_old, boxes_new, whole_sheet in changes:
        unpaired = oi is None or nj is None
        sheet, description = sheet_label(new[nj] if nj is not None else old[oi])
        pages = []
        if oi is not None and boxes_old:
            body.insert_pdf(old, from_page=oi, to_page=oi)
            mark(body[-1], boxes_old, MARK_OLD,
                 badge_text('ALT', revisions[0], oi + 1, len(boxes_old), whole_sheet, unpaired),
                 'nur in der alten Version')
            pages.append(body.page_count - 1)
        if nj is not None and boxes_new:
            body.insert_pdf(new, from_page=nj, to_page=nj)
            mark(body[-1], boxes_new, MARK_NEW,
                 badge_text('NEU', revisions[1], nj + 1, len(boxes_new), whole_sheet, unpaired),
                 'nur in der neuen Version')
            pages.append(body.page_count - 1)
        if not pages:
            continue
        if whole_sheet:
            marks = 'Blatt komplett neu' if oi is None else 'Blatt komplett entfallen'
        elif unpaired:
            marks = 'Umbruch: %d %s' % (len(boxes_new or boxes_old), 'gruen' if oi is None else 'rosa')
        else:
            marks = '%d rosa / %d gruen' % (len(boxes_old), len(boxes_new))
        rows.append(dict(sheet=sheet, description=description, body_pages=pages, whole_sheet=whole_sheet,
                         old='-' if oi is None else str(oi + 1), new='-' if nj is None else str(nj + 1),
                         marks=marks))
    return body, rows


def cover(out, rect, rows, names, counts):
    page = out.new_page(width=rect.width, height=rect.height)

    def text(x, y, value, size=10, color=(0, 0, 0), font='helv'):
        page.insert_text((x, y), value, fontsize=size, color=color, fontname=font)

    text(50, 60, 'Schemaaenderungen  %s  ->  %s' % names, 17, font='hebo')
    text(50, 86, 'Alt: %s (%d Blatt)      Neu: %s (%d Blatt)' % (names[0], counts[0], names[1], counts[1]), 11)
    text(50, 103, 'Erstellt: %s' % time.strftime('%d.%m.%Y'), 11)

    text(50, 140, 'Legende', 14, font='hebo')
    page.draw_rect(pymupdf.Rect(50, 150, 74, 166), color=(0.3, 0.3, 0.3), fill=MARK_OLD, width=0.6)
    text(86, 163, 'ALT  -  rosa markiert: was auf diesem Blatt entfernt oder ersetzt wurde', 11)
    page.draw_rect(pymupdf.Rect(50, 174, 74, 190), color=(0.3, 0.3, 0.3), fill=MARK_NEW, width=0.6)
    text(86, 187, 'NEU  -  gruen markiert: was auf diesem Blatt neu ist oder geaendert wurde', 11)
    text(86, 211, 'Inhalt, der nur auf ein anderes Blatt gerutscht ist, wird nicht markiert.', 11)
    text(86, 228, 'Ein komplett neues oder entfallenes Blatt ist nur bei der Blattnummer markiert.', 11)
    text(86, 245, 'Das Schriftfeld (Fusszeile: Bearbeiter, Datum, Revision ...) wird nicht verglichen.', 11)

    text(50, 275, 'Geaenderte Blaetter: %d' % len(rows), 14, font='hebo')
    y = 297
    for column, title in ((50, 'Seite'), (110, 'Blatt'), (200, 'Blatt alt'), (290, 'Blatt neu'),
                          (390, 'Markierte Stellen'), (510, 'Beschreibung')):
        text(column, y, title, 10, font='hebo')
    page.draw_line(pymupdf.Point(50, y + 4), pymupdf.Point(rect.width - 50, y + 4), width=0.6)
    y += 18
    for row in rows:
        if y > rect.height - 40:
            page = out.new_page(width=rect.width, height=rect.height)
            y = 60
        for column, value in ((50, row['pages']), (110, row['sheet']), (200, row['old']), (290, row['new']),
                              (390, row['marks']), (510, row['description'][:100])):
            text(column, y, value)
        y += 14


def build(changes, old, new, names, out_path):
    body, rows = build_body(changes, old, new, tuple(revision_of(name) for name in names))
    probe = pymupdf.open()
    cover(probe, new[0].rect, [dict(row, pages='') for row in rows], names, (old.page_count, new.page_count))
    offset = probe.page_count
    for row in rows:
        row['pages'] = ' + '.join(str(p + offset + 1) for p in row['body_pages'])

    out = pymupdf.open()
    cover(out, new[0].rect, rows, names, (old.page_count, new.page_count))
    out.insert_pdf(body)
    out.set_toc([[1, 'Uebersicht / Legende', 1]] +
                [[1, '%s  -  %s%s' % (row['sheet'], 'NEU: ' if row['whole_sheet'] else '', row['description'][:60]),
                  row['body_pages'][0] + offset + 1] for row in rows])
    out.set_metadata({'title': 'Schemaaenderungen %s -> %s' % names,
                      'subject': 'Rosa = alte Version, Gruen = neue Version'})
    out.save(out_path, deflate=True, garbage=3)
    return rows


BADGE = re.compile(r'(ALT|NEU)\s+(?:-\s+Rev\.\s+\S+\s+)?-\s+Blatt\s+(\d+)\s+-\s+(.*)')


def read_marks(out_path):
    """What the produced PDF really shows - the check must not trust the diff code."""
    doc = pymupdf.open(out_path)
    marks, whole = defaultdict(list), set()
    for page in doc:
        match = BADGE.match(page.get_text(clip=pymupdf.Rect(0, 0, page.rect.width * 0.45, 26)).strip())
        if not match:
            continue
        side, number, rest = match.group(1), int(match.group(2)) - 1, match.group(3)
        if 'komplett' in rest:
            whole.add((side, number))
        for annot in page.annots():
            marks[(side, number)].append(annot.rect)
    return marks, whole


def verify(old, new, out_path, report):
    """No difference may be missing: every row that exists in only one version must be marked."""
    marks, whole = read_marks(out_path)
    rows_old, rows_new = document_rows(old), document_rows(new)
    counts_old = Counter(token for _, token, _ in rows_old)
    counts_new = Counter(token for _, token, _ in rows_new)

    missing, checked = [], 0
    for side, rows, own, other in (('ALT', rows_old, counts_old, counts_new),
                                   ('NEU', rows_new, counts_new, counts_old)):
        for page_index, token, items in rows:
            if other[token]:
                continue
            checked += 1
            if (side, page_index) in whole:
                continue
            covered = any(rect.intersects(mark) for _, rect in items for mark in marks[(side, page_index)])
            if not covered:
                missing.append((side, page_index + 1, token))

    report('')
    report('Vollstaendigkeitspruefung')
    report('  Zeilen, die es nur in einer Version gibt: %d' % checked)
    report('  davon im Diff sichtbar markiert:          %d' % (checked - len(missing)))
    only_old = sum(1 for token, count in counts_old.items() if not counts_new[token] for _ in range(count))
    only_new = sum(1 for token, count in counts_new.items() if not counts_old[token] for _ in range(count))
    report('  nur in ALT: %d Zeilen | nur in NEU: %d Zeilen' % (only_old, only_new))
    differing = [(token, counts_old[token], counts_new[token]) for token in set(counts_old) | set(counts_new)
                 if counts_old[token] and counts_new[token] and counts_old[token] != counts_new[token]]
    report('  Zeilen in beiden Versionen, aber unterschiedlich oft (Layout/Wiederholung): %d' % len(differing))
    for token, a, b in sorted(differing)[:20]:
        report('     ALT %dx / NEU %dx  %s' % (a, b, token[:80]))
    if len(differing) > 20:
        report('     ... %d weitere' % (len(differing) - 20))
    for entry in missing:
        report('  FEHLT: %s Blatt %d  %s' % (entry[0], entry[1], entry[2][:90]))
    report('  ERGEBNIS: %s' % ('OK - keine Differenz ausgelassen' if not missing
                               else '%d DIFFERENZEN NICHT MARKIERT' % len(missing)))
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('old')
    parser.add_argument('new')
    parser.add_argument('-o', '--out')
    parser.add_argument('--report')
    args = parser.parse_args()

    lines = []

    def report(line=''):
        print(line, flush=True)
        lines.append(line)

    old, new = pymupdf.open(args.old), pymupdf.open(args.new)
    out_path = args.out or re.sub(r'\.pdf$', '', args.new, flags=re.I) + '_Aenderungen.pdf'
    names = (args.old.replace('\\', '/').split('/')[-1], args.new.replace('\\', '/').split('/')[-1])

    report('Alt: %s (%d Blatt)' % (names[0], old.page_count))
    report('Neu: %s (%d Blatt)' % (names[1], new.page_count))
    pairs = align_sheets(old, new)
    report('Blattzuordnung: %d Paare, %d nur alt, %d nur neu'
           % (sum(1 for a, b in pairs if a is not None and b is not None),
              sum(1 for a, b in pairs if b is None), sum(1 for a, b in pairs if a is None)))

    changes, shifted, dropped_graphics = collect_changes(old, new, pairs, report)
    rows = build(changes, old, new, names, out_path)
    report('')
    report('geschrieben: %s  (%d geaenderte Blaetter)' % (out_path, len(rows)))
    for row in rows:
        report('  Seite %-8s Blatt %-14s alt %-5s neu %-5s %-24s %s'
               % (row['pages'], row['sheet'], row['old'], row['new'], row['marks'], row['description'][:60]))

    report('')
    report('Als Verschiebung nicht markiert: %d Zeilen' % len(shifted))
    for side, sheet, token in shifted[:15]:
        report('  %s Blatt %d  %s' % (side, sheet, token[:80]))
    if len(shifted) > 15:
        report('  ... %d weitere' % (len(shifted) - 15))
    report('Als Layoutverschiebung verworfene Grafikstellen: %d' % len(dropped_graphics))

    missing = verify(old, new, out_path, report)

    if args.report:
        open(args.report, 'w', encoding='utf-8').write('\n'.join(lines))
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
