"""
conduit_graph.py -- the Graphical editor: a qpwgraph-style node canvas over the
same config the List editor edits.

Layout: one canvas split into boxes -- Speaker top-left, Microphone top-right,
the Custom Conduits in a row along the bottom -- with resizable dividers.  Each
box is its own QGraphicsView / QGraphicsScene (own zoom, own fullscreen).  Links
inside one box are scene items; links between boxes are drawn by a window-level
overlay between the two on-screen port positions (zoom differs per box), and
hit-tested by the views.

Mouse:
  wheel            zoom this box (anchored under the cursor)
  left drag port   make a link (drop on any compatible port, in any box);
                   the "Both" port makes the left + right links together
  left drag cell   move it; drop it on another box to move it there
  left drag empty  rectangle-select links (Ctrl+drag pans; middle never pans)
  click link       select (Shift adds)
  middle click     block / unblock a cell or link (in outcome mode: block -> unblock -> none)
  right click      context menus; on the background: New Cell / New Speaker
  double-click bg  fit the box
  Delete           delete selected links;   Esc  leave fullscreen / outcome mode / clear selection
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QRubberBand, QSplitter, QVBoxLayout, QWidget)

import qt_ui_kit as kit

import conduit_core as core
import conduit_dialogs as dlg
from conduit_header import BoxHeader

# ---------------------------------------------------------------------------
# look
# ---------------------------------------------------------------------------

COLOR_IN = QColor("#3b82f6")        # into a virtual device
COLOR_OUT = QColor("#f97316")       # out of a virtual device
COLOR_BYPASS = QColor("#22c55e")    # app straight to a real speaker
COLOR_DIRECT = QColor("#facc15")    # any other cell-to-cell link
COLOR_PENDING = QColor("#6e6e6e")   # configured, not linked in PipeWire (yet)
COLOR_BLOCKED = QColor("#6b1010")   # very dark red
COLOR_CLOSED = QColor("#a855f7")    # purple
COLOR_GHOST = QColor(255, 255, 255, 110)
COLOR_INTERNAL = QColor(255, 255, 255, 90)

CELL_W = 196
HEADER_H = 26
ROW_H = 20
PORT_W = 46
PORT_H = 16
BOTH_EXTRA = 38      # how far the Both pill reaches past L / R, into the cell
BOTTOM_PAD = 8
NS_GAP = 34
LINK_W = 2.2


def port_label(ch: str) -> str:
    return {"*": "Both", "FL": "L", "FR": "R", "MONO": "M"}.get(ch, ch)


def link_path(p1: QPointF, p2: QPointF) -> QPainterPath:
    path = QPainterPath(p1)
    dx = max(40.0, abs(p2.x() - p1.x()) * 0.5)
    path.cubicTo(QPointF(p1.x() + dx, p1.y()), QPointF(p2.x() - dx, p2.y()), p2)
    return path


def paint_link(p: QPainter, path: QPainterPath, color: QColor, *, selected=False, dashed=False,
               label: str = "", width: float = LINK_W, bg: QColor | None = None):
    p.setRenderHint(QPainter.Antialiasing)
    if selected:
        glow = QPen(QColor(255, 255, 255, 200), width + 4)
        glow.setCapStyle(Qt.RoundCap)
        p.strokePath(path, glow)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    if dashed:
        pen.setStyle(Qt.DashLine)
    if label:
        # a gap in the line with the value filling it
        mid = path.pointAtPercent(0.5)
        fm = QFontMetrics(p.font())
        w, h = fm.horizontalAdvance(label) + 10, fm.height() + 2
        gap = QRectF(mid.x() - w / 2, mid.y() - h / 2, w, h)
        clip = QPainterPath()
        clip.addRect(path.boundingRect().adjusted(-20, -20, 20, 20))
        hole = QPainterPath()
        hole.addRoundedRect(gap, 4, 4)
        p.save()
        p.setClipPath(clip.subtracted(hole))
        p.strokePath(path, pen)
        p.restore()
        p.setPen(QPen(color.lighter(130)))
        p.drawText(gap, Qt.AlignCenter, label)
    else:
        p.strokePath(path, pen)


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------

class CellItem(QGraphicsObject):
    def __init__(self, editor, box: str, cell_id: str, ghost_label: str | None = None):
        super().__init__()
        self.editor, self.box, self.cell_id = editor, box, cell_id
        self.ghost = ghost_label is not None
        self.hub = core.is_hub(cell_id)
        self.is_ns = cell_id.startswith("ns:")
        self.title = ghost_label or ""
        self.ins: list[str] = []
        self.outs: list[str] = []
        self.status = "active"
        self.volume_text = ""
        self.color: str | None = None
        self.also = ""
        self.speaker = False
        self.auto_detect = False
        self.events = 0
        self.outcome = None
        self.hover_port = None
        self._drag_link = False
        self._press_pos = None
        self.setFlag(QGraphicsItem.ItemIsMovable, not self.is_ns)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setCursor(Qt.ArrowCursor)

    # -- data
    def configure(self, title, ins, outs, status, volume_text="", color=None, also="", speaker=False,
                  auto_detect=False, events=0):
        changed_geom = (ins, outs) != (self.ins, self.outs)
        if changed_geom:
            self.prepareGeometryChange()
        self.title, self.ins, self.outs, self.status = title, list(ins), list(outs), status
        self.volume_text, self.color, self.also, self.speaker = volume_text, color, also, speaker
        self.auto_detect, self.events = auto_detect, events
        self.update()

    def chans(self, side):
        return list(self.ins if side == "in" else self.outs)

    def rows(self, side):
        """Every grabbable port on a side: "*" (Both) first when there are two or more channels."""
        chans = self.chans(side)
        return (["*"] + chans) if len(chans) >= 2 else chans

    def height(self) -> float:
        n = max(len(self.ins), len(self.outs), 1)
        return HEADER_H + n * ROW_H + BOTTOM_PAD + (12 if self.also else 0)

    def boundingRect(self) -> QRectF:
        top = -18 if self.volume_text or self.outcome else -2
        extra = NS_GAP if self.is_ns else 0
        return QRectF(-6, top - extra, CELL_W + 12, self.height() - top + 4 + extra)

    def body_rect(self) -> QRectF:
        return QRectF(0, 0, CELL_W, self.height())

    def port_rect(self, side, ch) -> QRectF | None:
        chans = self.chans(side)
        if ch == "*":
            # Both sits BEHIND the channel pills: as tall as all of them, reaching further into
            # the cell, so it shows in the gaps between L and R and as a tab on the inner side.
            if len(chans) < 2:
                return None
            first, last = self.port_rect(side, chans[0]), self.port_rect(side, chans[-1])
            r = first.united(last).adjusted(0, -3, 0, 3)
            return r.adjusted(0, 0, BOTH_EXTRA, 0) if side == "in" else r.adjusted(-BOTH_EXTRA, 0, 0, 0)
        if ch not in chans:
            return None
        i = chans.index(ch)
        y = HEADER_H + i * ROW_H + (ROW_H - PORT_H) / 2
        x = -4 if side == "in" else CELL_W - PORT_W + 4
        return QRectF(x, y, PORT_W, PORT_H)

    def resolve_port(self, side, ch) -> str | None:
        rows = self.rows(side)
        if not rows:
            return None
        if ch in rows:
            return ch
        chans = [c for c in rows if c != "*"]
        if ch == "*":
            return chans[0] if chans else rows[0]
        if len(chans) == 1:
            return chans[0]
        idx = core.channel_index(ch)
        return chans[idx] if idx < len(chans) else chans[0]

    def port_scene_pos(self, side, ch) -> QPointF:
        r = self.port_rect(side, self.resolve_port(side, ch) or "")
        if r is None:
            local = QPointF(0 if side == "in" else CELL_W, HEADER_H / 2)
        else:
            local = QPointF(r.left() if side == "in" else r.right(), r.center().y())
        return self.mapToScene(local)

    def port_at(self, local: QPointF):
        for side in ("in", "out"):            # the channel pills sit on top of Both
            for ch in self.chans(side):
                r = self.port_rect(side, ch)
                if r is not None and r.adjusted(-3, -1, 3, 1).contains(local):
                    return side, ch
        for side in ("in", "out"):
            r = self.port_rect(side, "*")
            if r is not None and r.adjusted(-3, 0, 3, 0).contains(local):
                return side, "*"
        return None

    # -- paint
    def paint(self, p: QPainter, option, widget=None):
        th = kit.Theme()
        p.setRenderHint(QPainter.Antialiasing)
        dim = self.status in ("closed", "inactive") or self.editor.outcome_box == self.box
        p.setOpacity(0.55 if dim else 1.0)
        if self.ghost:
            p.setOpacity(0.5)
        body = self.body_rect()
        radius = min(th.corner_radius, 10)
        fill = QColor(th.surface())
        head = QColor(self.color) if self.color else QColor(th.accent())
        border = QColor(th.border())
        if self.hub:
            border = QColor(th.outline())
            head = QColor(self.color) if self.color else QColor(th.secondary())
        if self.status == "blocked":
            fill, head, border = COLOR_BLOCKED.darker(160), COLOR_BLOCKED, COLOR_BLOCKED.lighter(150)
        elif self.status == "closed":
            border = COLOR_CLOSED
            head = QColor(COLOR_CLOSED).darker(220) if not self.color else head
        if self.editor.outcome_box == self.box:
            fill, head, border = QColor("#1c1c1c"), QColor("#2a2a2a"), QColor("#3a3a3a")

        if self.is_ns:   # the split: a connector in the gap above, from the hub this hangs off
            pen = QPen(COLOR_INTERNAL, 2, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(CELL_W / 2, -NS_GAP + 2), QPointF(CELL_W / 2, -2))

        path = kit.rounded_rect_path(body, radius)
        p.fillPath(path, fill)
        hp = kit.rounded_rect_path(QRectF(0, 0, CELL_W, HEADER_H), radius, bottom_left=False, bottom_right=False)
        p.fillPath(hp, head)
        pen = QPen(border, 1.6 if self.hub else 1.2)
        if self.ghost:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        if self.editor.is_cell_selected(self.cell_id):
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawPath(path)

        # title
        f = QFont(p.font())
        f.setBold(True)
        f.setPointSizeF(max(7.0, f.pointSizeF()))
        p.setFont(f)
        fm = QFontMetrics(f)
        badges = []
        if self.speaker:
            badges.append("🔊" if fm.inFont("🔊") else "SPK")
        if self.auto_detect:
            badges.append("^")
        if self.events:
            badges.append(f"⚡{self.events}" if fm.inFont("⚡") else f"E{self.events}")
        badge_txt = " ".join(badges)
        bw = fm.horizontalAdvance(badge_txt) + 8 if badge_txt else 0
        title_col = kit.contrast_text(head)
        if self.status == "closed" and not self.color:
            title_col = COLOR_CLOSED.lighter(140)
        p.setPen(title_col)
        title = fm.elidedText(self.title, Qt.ElideRight, int(CELL_W - 16 - bw))
        p.drawText(QRectF(8, 0, CELL_W - 16 - bw, HEADER_H), Qt.AlignVCenter | Qt.AlignLeft, title)
        if badge_txt:
            p.drawText(QRectF(CELL_W - bw - 6, 0, bw, HEADER_H), Qt.AlignVCenter | Qt.AlignRight, badge_txt)

        # ports
        pf = QFont(p.font())
        pf.setBold(False)
        pf.setPointSizeF(max(6.5, pf.pointSizeF() * 0.85))
        p.setFont(pf)
        for side in ("in", "out"):
            both = self.port_rect(side, "*")
            if both is not None:
                hovered = self.hover_port == (side, "*")
                bfill = QColor(th.secondary()).lighter(135 if hovered else 100)
                p.setPen(QPen(QColor(th.border()).lighter(130), 1))
                p.setBrush(bfill)
                p.drawRoundedRect(both, 6, 6)
                tab = (QRectF(both.right() - BOTH_EXTRA, both.top(), BOTH_EXTRA, both.height()) if side == "in"
                       else QRectF(both.left(), both.top(), BOTH_EXTRA, both.height()))
                p.setPen(kit.contrast_text(bfill))
                p.drawText(tab, Qt.AlignCenter, "Both")
            for ch in self.chans(side):
                r = self.port_rect(side, ch)
                hovered = self.hover_port == (side, ch)
                pfill = QColor(th.accent()).lighter(170 if hovered else 125)
                p.setPen(QPen(QColor(th.border()).lighter(130), 1))
                p.setBrush(pfill)
                p.drawRoundedRect(r, 5, 5)
                p.setPen(kit.contrast_text(pfill))
                p.drawText(r, Qt.AlignCenter, port_label(ch))
        p.setBrush(Qt.NoBrush)

        if self.also:
            p.setPen(QColor(255, 255, 255, 120))
            sf = QFont(pf)
            sf.setItalic(True)
            p.setFont(sf)
            p.drawText(QRectF(8, self.height() - 16, CELL_W - 16, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(sf).elidedText(self.also, Qt.ElideRight, CELL_W - 16))

        # value above the cell: volume override, or the outcome badge in outcome mode
        p.setOpacity(1.0)
        top = None
        if self.editor.outcome_box == self.box and not self.hub:
            if self.outcome == "block":
                top, col = "BLOCK", QColor("#ef4444")
            elif self.outcome == "unblock":
                top, col = "UNBLOCK", QColor("#22c55e")
            else:
                top, col = "middle-click", QColor(255, 255, 255, 90)
        elif self.volume_text:
            top, col = self.volume_text, QColor(th.text())
        if top:
            vf = QFont(pf)
            vf.setBold(True)
            p.setFont(vf)
            p.setPen(col)
            p.drawText(QRectF(0, -18, CELL_W, 16), Qt.AlignCenter, top)

    # -- interaction
    def hoverMoveEvent(self, e):
        hp = self.port_at(e.pos())
        if hp != self.hover_port:
            self.hover_port = hp
            self.setCursor(Qt.CrossCursor if hp else Qt.ArrowCursor)
            self.update()

    def hoverLeaveEvent(self, e):
        self.hover_port = None
        self.update()

    def mousePressEvent(self, e):
        self.editor.interacting = True
        if e.button() == Qt.MiddleButton:
            self.editor.cell_middle_click(self)
            e.accept()
            return
        if e.button() == Qt.LeftButton and not self.ghost:
            hp = self.port_at(e.pos())
            if hp is not None and self.editor.outcome_box is None:
                self._drag_link = True
                self.editor.begin_link_drag(self, hp[0], hp[1], e.screenPos())
                e.accept()
                return
        if e.button() == Qt.LeftButton:
            self._press_pos = QPointF(self.pos())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_link:
            self.editor.update_link_drag(e.screenPos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.editor.interacting = False
        if self._drag_link:
            self._drag_link = False
            self.editor.end_link_drag(e.screenPos())
            e.accept()
            return
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton and self._press_pos is not None:
            moved = (self.pos() - self._press_pos).manhattanLength() > 2
            self._press_pos = None
            if moved:
                self.editor.cell_released(self, e.screenPos())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.editor.cell_moved(self)
        return super().itemChange(change, value)


class LinkItem(QGraphicsItem):
    """A link drawn inside one box's scene (both ends in that box)."""

    def __init__(self, editor, key: str, ghost: bool = False, internal: bool = False):
        super().__init__()
        self.editor, self.key, self.ghost, self.internal = editor, key, ghost, internal
        self.path = QPainterPath()
        self.setZValue(0)
        self.setAcceptedMouseButtons(Qt.NoButton if internal else (Qt.LeftButton | Qt.MiddleButton | Qt.RightButton))

    def set_points(self, p1, p2):
        self.prepareGeometryChange()
        self.path = link_path(p1, p2)

    def boundingRect(self):
        return self.path.boundingRect().adjusted(-30, -20, 30, 20)

    def shape(self):
        s = QPainterPathStroker()
        s.setWidth(12)
        return s.createStroke(self.path)

    def paint(self, p, option, widget=None):
        if self.internal:
            paint_link(p, self.path, COLOR_INTERNAL, dashed=True, width=1.6)
            return
        if self.ghost:
            paint_link(p, self.path, COLOR_GHOST, dashed=True, width=1.6)
            return
        color, label = self.editor.link_look(self.key)
        paint_link(p, self.path, color, selected=self.key in self.editor.selected, label=label)

    def mousePressEvent(self, e):
        if self.ghost:
            e.ignore() if e.button() != Qt.RightButton else e.accept()
            return
        if e.button() == Qt.LeftButton:
            self.editor.select_link(self.key, additive=bool(e.modifiers() & Qt.ShiftModifier))
            e.accept()
        elif e.button() == Qt.MiddleButton:
            self.editor.toggle_link_block([self.key])
            e.accept()
        else:
            e.accept()


# ---------------------------------------------------------------------------
# one box
# ---------------------------------------------------------------------------

class BoxView(QGraphicsView):
    def __init__(self, editor, box_widget, box: str):
        self.scene_ = QGraphicsScene()
        super().__init__(self.scene_)
        self.editor, self.box_widget, self.box = editor, box_widget, box
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.zoom = 1.0
        self._band = None
        self._band_origin = None
        self._pan_last = None
        self.cells: dict[str, CellItem] = {}
        self.links: dict[str, LinkItem] = {}
        self._restored = False
        self._view_save = QTimer(self)
        self._view_save.setSingleShot(True)
        self._view_save.setInterval(400)
        self._view_save.timeout.connect(self._save_view)
        self.scene_.setSceneRect(QRectF(-3000, -3000, 6000, 6000))
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    # -- persisted zoom / centre
    def restore_view(self):
        v = self.editor.c.layout.view(self.box)
        z = float(v.get("zoom", 1.0) or 1.0)
        self.set_zoom(z)
        c = v.get("center")
        if isinstance(c, list) and len(c) == 2:
            self.centerOn(QPointF(c[0], c[1]))
        else:
            self.fit()
        self._restored = True

    def _save_view(self):
        if not self._restored:
            return
        c = self.mapToScene(self.viewport().rect().center())
        self.editor.c.layout.set_view(self.box, self.zoom, c.x(), c.y())

    def _scrolled(self, *_):
        self.editor.overlay.update()
        self._view_save.start()

    def set_zoom(self, z):
        z = max(0.25, min(3.0, z))
        self.setTransform(self.transform().fromScale(z, z))
        self.zoom = z
        self.editor.overlay.update()
        self._view_save.start()

    def fit(self):
        items = [i for i in self.cells.values()]
        if not items:
            self.set_zoom(1.0)
            self.centerOn(0, 0)
            return
        r = QRectF()
        for i in items:
            r = r.united(i.sceneBoundingRect())
        r = r.adjusted(-40, -40, 40, 40)
        vw, vh = max(1, self.viewport().width()), max(1, self.viewport().height())
        z = min(vw / r.width(), vh / r.height(), 1.25)
        self.set_zoom(z)
        self.centerOn(r.center())

    # -- painting
    def drawBackground(self, p, rect):
        th = kit.Theme()
        p.fillRect(rect, th.window())
        step = 40
        p.setPen(QPen(QColor(255, 255, 255, 14), 1.2))
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        x = left
        pts = []
        while x < rect.right():
            y = top
            while y < rect.bottom():
                pts.append(QPointF(x, y))
                y += step
            x += step
        if len(pts) < 20000:
            p.drawPoints(pts)

    def drawForeground(self, p, rect):
        if not core.box_enabled(self.editor.c.state, self.box):
            p.save()
            p.resetTransform()
            p.fillRect(self.viewport().rect(), QColor(0, 0, 0, 150))
            p.setPen(QColor(255, 255, 255, 140))
            p.drawText(self.viewport().rect().adjusted(0, 0, 0, -12), Qt.AlignHCenter | Qt.AlignBottom,
                       "Disabled — right-click the hub to enable")
            p.restore()

    # -- input
    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        self.set_zoom(self.zoom * (1.15 ** (dy / 120.0)))
        e.accept()

    def mousePressEvent(self, e):
        self.editor.interacting = True
        item = self.itemAt(e.position().toPoint())
        on_cell = isinstance(item, CellItem)
        if not on_cell:
            hit = self.editor.cross_link_at(self.viewport().mapTo(self.editor, e.position().toPoint()))
            if hit is not None and not isinstance(item, LinkItem):
                if e.button() == Qt.LeftButton:
                    self.editor.select_link(hit, additive=bool(e.modifiers() & Qt.ShiftModifier))
                elif e.button() == Qt.MiddleButton:
                    self.editor.toggle_link_block([hit])
                e.accept()
                return
        if item is None:
            if e.button() == Qt.LeftButton and e.modifiers() & Qt.ControlModifier:
                self._pan_last = e.position()
                self.viewport().setCursor(Qt.ClosedHandCursor)
                e.accept()
                return
            if e.button() == Qt.LeftButton:
                self._band_origin = e.position().toPoint()
                self._band = QRubberBand(QRubberBand.Rectangle, self.viewport())
                self._band.setGeometry(QRect(self._band_origin, self._band_origin))
                self._band.show()
                e.accept()
                return
            if e.button() == Qt.MiddleButton:
                e.accept()          # middle click never pans
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan_last is not None:
            d = e.position() - self._pan_last
            self._pan_last = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._band is not None:
            self._band.setGeometry(QRect(self._band_origin, e.position().toPoint()).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.editor.interacting = False
        if self._pan_last is not None:
            self._pan_last = None
            self.viewport().unsetCursor()
            return
        if self._band is not None:
            r = self._band.geometry()
            self._band.hide()
            self._band.deleteLater()
            self._band = None
            additive = bool(e.modifiers() & Qt.ShiftModifier)
            if r.width() < 4 and r.height() < 4:
                if not additive:
                    self.editor.clear_selection()
            else:
                self.editor.select_rect(self, r, additive)
            self.editor.flush_pending_sync()
            return
        super().mouseReleaseEvent(e)
        self.editor.flush_pending_sync()

    def mouseDoubleClickEvent(self, e):
        if self.itemAt(e.position().toPoint()) is None:
            self.fit()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        pos = e.pos()
        item = self.itemAt(pos)
        gpos = e.globalPos()
        if isinstance(item, CellItem):
            self.editor.cell_menu(item, gpos)
        elif isinstance(item, LinkItem) and not item.internal:
            self.editor.link_menu(item.key, gpos)
        else:
            hit = self.editor.cross_link_at(self.viewport().mapTo(self.editor, pos))
            if hit is not None:
                self.editor.link_menu(hit, gpos)
            else:
                self.editor.background_menu(self, self.mapToScene(pos), gpos)
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.editor.overlay.update()


class BoxWidget(QWidget):
    def __init__(self, editor, box: str):
        super().__init__()
        self.editor, self.box = editor, box
        self.setAutoFillBackground(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(2)
        self.header = BoxHeader(editor.c, box, fullscreen=True)
        self.header.fullscreen_clicked.connect(lambda: editor.toggle_fullscreen(box))
        v.addWidget(self.header)
        self.banner = QWidget()
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(6, 2, 6, 2)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        bh.addWidget(self.banner_text, 1)
        done = kit.CustomButton("Done")
        done.clicked.connect(lambda: editor.finish_outcome(True))
        cancel = kit.CustomButton("Cancel")
        cancel.clicked.connect(lambda: editor.finish_outcome(False))
        bh.addWidget(cancel)
        bh.addWidget(done)
        self.banner.hide()
        v.addWidget(self.banner)
        self.view = BoxView(editor, self, box)
        v.addWidget(self.view, 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        th = kit.Theme()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = kit.rounded_rect_path(r, th.corner_radius)
        p.fillPath(path, th.base())
        p.setPen(QPen(th.border(), 1.2))
        p.drawPath(path)


# ---------------------------------------------------------------------------
# overlay for cross-box links and the link being dragged
# ---------------------------------------------------------------------------

class Overlay(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        for bw in self.editor.visible_box_widgets():
            vp = bw.view.viewport()
            clip.addRect(QRectF(QRect(vp.mapTo(self.editor, QPoint(0, 0)), vp.size())))
        p.setClipPath(clip)
        for key, path, kind in self.editor.cross_paths():
            if kind == "ghost":
                paint_link(p, path, COLOR_GHOST, dashed=True, width=1.6)
            else:
                color, label = self.editor.link_look(key)
                paint_link(p, path, color, selected=key in self.editor.selected, label=label)
        d = self.editor.drag
        if d is not None and d.get("cursor") is not None:
            p.setClipping(False)
            start = d["start_pos"]()
            end = QPointF(self.editor.mapFromGlobal(d["cursor"]))
            path = link_path(start, end) if d["side"] == "out" else link_path(end, start)
            paint_link(p, path, QColor(255, 255, 255, 220), dashed=True, width=2)


# ---------------------------------------------------------------------------
# the editor
# ---------------------------------------------------------------------------

class GraphEditor(QWidget):
    show_unmanaged_changed = Signal(bool)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.selected: set[str] = set()
        self.selected_cells: set[str] = set()
        self.drag = None
        self.interacting = False
        self._menus_open = 0
        self._pending_sync = False
        self.outcome = None
        self.outcome_box = None
        self.box_widgets: dict[str, BoxWidget] = {}
        self.fullscreen_box = None
        self._cross: list = []        # [(key, src_item, src_side_ch, dst_item, dst_ch, kind)]
        self.ghost_links: dict = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(8)
        v.addWidget(self.vsplit)
        self.top = QSplitter(Qt.Horizontal)
        self.top.setChildrenCollapsible(False)
        self.top.setHandleWidth(8)
        self.vsplit.addWidget(self.top)

        self.bottom = QWidget()
        bv = QVBoxLayout(self.bottom)
        bv.setContentsMargins(0, 4, 0, 0)
        bv.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        lab = QLabel("Custom Conduits")
        lab.setStyleSheet("QLabel { font-weight: bold; }")
        row.addWidget(lab)
        row.addWidget(dlg.dim_label("your own virtual patch points", wrap=False))
        row.addStretch(1)
        add = kit.CustomButton("+ Custom Conduit")
        add.clicked.connect(self._add_conduit)
        row.addWidget(add)
        bv.addLayout(row)
        self.custom_split = QSplitter(Qt.Horizontal)
        self.custom_split.setChildrenCollapsible(False)
        self.custom_split.setHandleWidth(8)
        bv.addWidget(self.custom_split, 1)
        self.empty_custom = dlg.dim_label("No Custom Conduits yet — “+ Custom Conduit” adds one.")
        self.empty_custom.setAlignment(Qt.AlignCenter)
        bv.addWidget(self.empty_custom, 1)
        self.vsplit.addWidget(self.bottom)

        for name, sp in (("graph.vertical", self.vsplit), ("graph.top", self.top), ("graph.custom", self.custom_split)):
            sp.splitterMoved.connect(lambda *_a, n=name, s=sp: (self.c.layout.set_splitter(n, s.sizes()),
                                                                 self.overlay.update()))

        self.overlay = Overlay(self)
        self.overlay.raise_()

        for key, fn in ((QKeySequence.Delete, self.delete_selected), (Qt.Key_Backspace, self.delete_selected),
                        (Qt.Key_Escape, self.escape)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        self.c.state_changed.connect(self.sync)
        self.c.live_changed.connect(self.sync)
        self.sync()
        QTimer.singleShot(0, self._restore_layout)

    # ------------------------------------------------------------ structure
    def _restore_layout(self):
        for name, sp, default in (("graph.vertical", self.vsplit, [620, 360]), ("graph.top", self.top, [500, 500])):
            sizes = self.c.layout.splitter(name)
            sp.setSizes(sizes if sizes and len(sizes) == sp.count() else default)
        sizes = self.c.layout.splitter("graph.custom")
        if sizes and len(sizes) == self.custom_split.count():
            self.custom_split.setSizes(sizes)
        for bw in self.box_widgets.values():
            bw.view.restore_view()
        self.overlay.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()

    def visible_box_widgets(self):
        return [bw for bw in self.box_widgets.values() if bw.isVisible()]

    def _ensure_boxes(self):
        boxes = core.all_boxes(self.c.state)
        for box in list(self.box_widgets):
            if box not in boxes:
                bw = self.box_widgets.pop(box)
                bw.hide()
                bw.setParent(None)
                bw.deleteLater()
                if self.fullscreen_box == box:
                    self.fullscreen_box = None
        new = []
        for box in boxes:
            if box not in self.box_widgets:
                bw = BoxWidget(self, box)
                self.box_widgets[box] = bw
                (self.top if box in ("speaker", "mic") else self.custom_split).addWidget(bw)
                new.append(bw)
        has_custom = any(b.startswith("custom.") for b in boxes)
        self.custom_split.setVisible(has_custom)
        self.empty_custom.setVisible(not has_custom)
        for bw in new:
            QTimer.singleShot(0, bw.view.restore_view)
        self._apply_fullscreen()

    def _add_conduit(self):
        core.add_conduit(self.c.state)
        self.c.commit()

    # ------------------------------------------------------------ fullscreen
    def toggle_fullscreen(self, box):
        self.fullscreen_box = None if self.fullscreen_box == box else box
        self._apply_fullscreen()

    def _apply_fullscreen(self):
        fs = self.fullscreen_box
        for box, bw in self.box_widgets.items():
            bw.setVisible(fs is None or box == fs)
        top_fs = fs in ("speaker", "mic")
        self.top.setVisible(fs is None or top_fs)
        self.bottom.setVisible(fs is None or not top_fs)
        for bw in self.box_widgets.values():
            if hasattr(bw.header, "fs"):
                bw.header.fs.setText("🗗" if bw.box == fs and bw.header.fs.fontMetrics().inFont("🗗") else "⛶")
        QTimer.singleShot(0, self.overlay.update)

    def escape(self):
        if self.outcome is not None:
            self.finish_outcome(False)
        elif self.fullscreen_box is not None:
            self.toggle_fullscreen(self.fullscreen_box)
        else:
            self.clear_selection()

    # ------------------------------------------------------------ sync
    def flush_pending_sync(self):
        if self._pending_sync and not self.interacting and self._menus_open == 0:
            self._pending_sync = False
            self.sync()

    def sync(self):
        if self.interacting or self._menus_open or self.drag is not None:
            self._pending_sync = True
            return
        self._ensure_boxes()
        st = self.c.state
        for bw in self.box_widgets.values():
            bw.header.sync()
        # which cells sit in which box
        placement = {}
        labels_in_boxes = {}
        for box in core.all_boxes(st):
            ids = core.hub_cells_for_box(st, box) + [c["id"] for c in st["cells"] if c["box"] == box]
            placement[box] = ids
        for c in st["cells"]:
            labels_in_boxes.setdefault(c["label"], set()).add(c["box"])
        for box, ids in placement.items():
            self._sync_box_cells(self.box_widgets[box].view, box, ids, labels_in_boxes)
        self._sync_links()
        self.selected &= {l["id"] for l in st["links"]}
        for bw in self.box_widgets.values():
            bw.view.viewport().update()
        self.overlay.update()

    def _sync_box_cells(self, view, box, ids, labels_in_boxes):
        st = self.c.state
        want = set(ids)
        for cid in list(view.cells):
            if cid not in want and not cid.startswith("ghost:"):
                it = view.cells.pop(cid)
                view.scene_.removeItem(it)
        # hubs first (ns cells hang off a hub), then cells
        for cid in ids:
            it = view.cells.get(cid)
            if it is None:
                it = CellItem(self, box, cid)
                view.cells[cid] = it
                if cid.startswith("ns:"):
                    parent_id = core.HUB_MIC if box == "mic" else core.hub_custom_mic(box.split(".")[1])
                    parent = view.cells.get(parent_id)
                    if parent is not None:
                        it.setParentItem(parent)
                    else:
                        view.scene_.addItem(it)
                else:
                    view.scene_.addItem(it)
            self._configure_cell(it, labels_in_boxes)
        for cid in ids:
            self._place(view, view.cells[cid])
        # ghosts are rebuilt in _sync_links
        return view

    def _configure_cell(self, it: CellItem, labels_in_boxes):
        st = self.c.state
        ports = self.c.cell_ports(it.cell_id)
        status = self.c.cell_status(it.cell_id)
        if it.hub:
            title = core.hub_display_name(st, it.cell_id)
            vol = ""
            hub = st.get(it.box) if it.box in ("speaker", "mic") else core.conduit_by_box(st, it.box)
            if hub and not it.cell_id.endswith(".mic") and not it.is_ns and hub.get("volume", 1.0) != 1.0:
                vol = dlg.format_volume(hub["volume"])
            if hub and hub.get("muted") and not it.is_ns and not it.cell_id.endswith(".mic"):
                vol = (vol + " " if vol else "") + "muted"
            it.configure(title, ports["in"], ports["out"], status, vol)
        else:
            cell = core.cell_by_id(st, it.cell_id)
            others = sorted(core.box_label(st, b) for b in labels_in_boxes.get(cell["label"], set()) if b != it.box)
            it.configure(cell["label"], ports["in"], ports["out"], status,
                         dlg.format_volume(cell["volume"]) if cell.get("volume", 1.0) != 1.0 else "",
                         cell.get("color"), ("also in " + ", ".join(others)) if others else "",
                         cell.get("speaker", False), dlg.auto_detect_active(cell.get("auto_detect", {})),
                         len(cell.get("events", [])))
        if it.is_ns and it.parentItem() is not None:
            it.setPos(0, it.parentItem().height() + NS_GAP)

    def _place(self, view, it: CellItem):
        if it.is_ns:
            return
        pos = self.c.layout.pos(it.cell_id)
        if pos is not None:
            if (it.pos().x(), it.pos().y()) != pos:
                it.setPos(*pos)
            return
        if getattr(it, "_placed", False):
            return
        it._placed = True
        st = self.c.state
        if it.hub:
            if it.cell_id.endswith(".mic"):
                base = view.cells.get(core.hub_custom(it.box.split(".")[1]))
                y = (base.height() + 60) if base is not None else 140
                it.setPos(0, y)
            else:
                it.setPos(0, 0)
        else:
            to_hub = any(l["src"] == it.cell_id and core.is_hub(l["dst"]) for l in st["links"])
            from_hub = any(l["dst"] == it.cell_id and core.is_hub(l["src"]) for l in st["links"])
            x = -(CELL_W + 140) if (to_hub or not from_hub) and not it.speaker else CELL_W + 140
            if it.speaker:
                x = CELL_W + 140
            y = -40.0
            for other in view.cells.values():
                if other is it or other.is_ns or not getattr(other, "_placed", True):
                    continue
                if abs(other.pos().x() - x) < 10:
                    y = max(y, other.pos().y() + other.height() + 24)
            it.setPos(x, y)
        self.c.layout.set_pos(it.cell_id, it.pos().x(), it.pos().y())

    def place_new_cell(self, cell_id, box, scene_pos: QPointF):
        self.c.layout.set_pos(cell_id, scene_pos.x() - CELL_W / 2, scene_pos.y() - HEADER_H / 2)

    # ------------------------------------------------------------ links
    def _item_for(self, cell_id):
        st = self.c.state
        box = core.box_of_hub(cell_id) if core.is_hub(cell_id) else (core.cell_by_id(st, cell_id) or {}).get("box")
        bw = self.box_widgets.get(box)
        if bw is None:
            return None
        return bw.view.cells.get(cell_id)

    def _sync_links(self):
        st = self.c.state
        self._cross = []
        wanted = {bw.box: {} for bw in self.box_widgets.values()}   # box -> key -> (src, sch, dst, dch, kind)
        for l in st["links"]:
            a, b = self._item_for(l["src"]), self._item_for(l["dst"])
            if a is None or b is None:
                continue
            if a.box == b.box:
                wanted[a.box][l["id"]] = (a, l["src_port"], b, l["dst_port"], "link")
            else:
                self._cross.append((l["id"], a, l["src_port"], b, l["dst_port"], "link"))
        # hub plumbing
        for box in wanted:
            for a_id, b_id in self._internal_pairs(box):
                a, b = self._item_for(a_id), self._item_for(b_id)
                if a is not None and b is not None:
                    wanted[box][f"int:{a_id}>{b_id}"] = (a, "*", b, "*", "internal")
        # unmanaged links
        self._sync_ghosts(wanted)
        for box, want in wanted.items():
            view = self.box_widgets[box].view
            for key in list(view.links):
                if key not in want:
                    view.scene_.removeItem(view.links.pop(key))
            for key, (a, sch, b, dch, kind) in want.items():
                it = view.links.get(key)
                if it is None:
                    it = LinkItem(self, key, ghost=kind == "ghost", internal=kind == "internal")
                    view.links[key] = it
                    view.scene_.addItem(it)
                it.ends = (a, sch, b, dch)
                it.set_points(a.port_scene_pos("out", sch), b.port_scene_pos("in", dch))
                it.update()

    def _internal_pairs(self, box):
        st = self.c.state
        out = []
        if box == "mic" and core.ns_method(st, "mic") in core.NOISE_SUPPRESSION_METHODS:
            out.append((core.HUB_MIC, core.NS_MIC))
        c = core.conduit_by_box(st, box)
        if c and c.get("as_microphone"):
            base, tap = core.hub_custom(c["id"]), core.hub_custom_mic(c["id"])
            if core.ns_method(st, box) in core.NOISE_SUPPRESSION_METHODS:
                out += [(base, core.ns_cell_id(box))]
            else:
                out.append((base, tap))
        return out

    def _sync_ghosts(self, wanted):
        st, snap = self.c.state, self.c.snap
        show = bool(self.c.layout.ui("show_unmanaged"))
        self.ghost_links = {}
        ghost_items_wanted = {box: set() for box in wanted}
        if show and snap.ok:
            node_cells = {}       # node id -> [(box, cell_id)]
            for c in st["cells"]:
                for n in core.cell_all_nodes(st, c["id"], snap):
                    node_cells.setdefault(n.id, []).append((c["box"], c["id"]))
            for box in core.all_boxes(st):
                for h in core.hub_cells_for_box(st, box):
                    for side in ("out", "in"):
                        n = core.hub_node(st, h, side, snap)
                        if n is not None and (box, h) not in node_cells.get(n.id, []):
                            node_cells.setdefault(n.id, []).append((box, h))
            for o, i in core.unmanaged_links(st, snap, self.c.plan):
                no, ni = snap.nodes[snap.port_owner[o]], snap.nodes[snap.port_owner[i]]
                sch, dch = snap.port_channel[o], snap.port_channel[i]
                src_opts, dst_opts = node_cells.get(no.id, []), node_cells.get(ni.id, [])
                pair = None
                for sb, sc in src_opts:
                    for db, dc in dst_opts:
                        if sb == db:
                            pair = ((sb, sc), (db, dc))
                            break
                    if pair:
                        break
                if pair is None:
                    if src_opts and dst_opts:
                        pair = (src_opts[0], dst_opts[0])
                    elif src_opts:
                        pair = (src_opts[0], (src_opts[0][0], None))
                    elif dst_opts:
                        pair = ((dst_opts[0][0], None), dst_opts[0])
                    else:
                        continue
                (sb, sc), (db, dc) = pair
                if sc is None:
                    sc = f"ghost:{sb}:{no.display_label}"
                    ghost_items_wanted[sb].add((sc, no.display_label, "out"))
                if dc is None:
                    dc = f"ghost:{db}:{ni.display_label}"
                    ghost_items_wanted[db].add((dc, ni.display_label, "in"))
                key = f"g:{sc}:{sch}>{dc}:{dch}"
                self.ghost_links[key] = {"src": sc, "src_port": sch, "dst": dc, "dst_port": dch,
                                         "src_box": sb, "dst_box": db,
                                         "src_label": no.display_label, "dst_label": ni.display_label}
        # ghost cells
        for box, items in ghost_items_wanted.items():
            view = self.box_widgets[box].view
            want_ids = {gid for gid, _l, _s in items}
            for cid in [k for k in view.cells if k.startswith("ghost:")]:
                if cid not in want_ids:
                    view.scene_.removeItem(view.cells.pop(cid))
            for gid, label, side in sorted(items):
                it = view.cells.get(gid)
                if it is None:
                    it = CellItem(self, box, gid, ghost_label=label)
                    view.cells[gid] = it
                    view.scene_.addItem(it)
                    it.setFlag(QGraphicsItem.ItemIsMovable, True)
                    self._place_ghost(view, it, side)
                known = self.c.layout.known(label)
                chans = {"out": known.get("out") or ["FL", "FR"], "in": known.get("in") or ["FL", "FR"]}
                it.configure(label, chans["in"] if side == "in" else [], chans["out"] if side == "out" else [],
                             "active")
        for key, g in self.ghost_links.items():
            a, b = self._ghost_or_item(g["src"], g["src_box"]), self._ghost_or_item(g["dst"], g["dst_box"])
            if a is None or b is None:
                continue
            if a.box == b.box:
                wanted[a.box][key] = (a, g["src_port"], b, g["dst_port"], "ghost")
            else:
                self._cross.append((key, a, g["src_port"], b, g["dst_port"], "ghost"))

    def _ghost_or_item(self, cid, box):
        if cid.startswith("ghost:"):
            return self.box_widgets[box].view.cells.get(cid)
        return self._item_for(cid)

    def _place_ghost(self, view, it, side):
        xs = [c.pos().x() for c in view.cells.values() if c is not it] or [0]
        x = (min(xs) - CELL_W - 160) if side == "out" else (max(xs) + CELL_W + 160)
        y = -40.0
        for other in view.cells.values():
            if other is not it and abs(other.pos().x() - x) < 10:
                y = max(y, other.pos().y() + other.height() + 24)
        it.setPos(x, y)

    def cross_paths(self):
        out = []
        for key, a, sch, b, dch, kind in self._cross:
            if not (a.scene() and b.scene()):
                continue
            pa = self._to_editor(a, a.port_scene_pos("out", sch))
            pb = self._to_editor(b, b.port_scene_pos("in", dch))
            if pa is None or pb is None:
                continue
            out.append((key, link_path(pa, pb), kind))
        return out

    def _to_editor(self, item, scene_pt):
        bw = self.box_widgets.get(item.box)
        if bw is None or not bw.isVisible():
            return None
        vp = bw.view.mapFromScene(scene_pt)
        return QPointF(bw.view.viewport().mapTo(self, vp))

    def cross_link_at(self, editor_pt: QPoint):
        s = QPainterPathStroker()
        s.setWidth(12)
        for key, path, kind in reversed(self.cross_paths()):
            if kind == "link" and s.createStroke(path).contains(QPointF(editor_pt)):
                return key
        return None

    def link_look(self, key):
        """(colour, gap label) for a configured link."""
        st = self.c.state
        l = core.link_by_id(st, key)
        if l is None:
            return COLOR_PENDING, ""
        label = ""
        if l.get("volume", 1.0) != 1.0:
            label = dlg.format_volume(l["volume"])
        if l.get("blocked"):
            return COLOR_BLOCKED, label
        if not self.c.link_live(key):
            return COLOR_PENDING, label
        if l.get("color"):
            return QColor(l["color"]), label
        if core.is_hub(l["dst"]):
            return COLOR_IN, label
        if core.is_hub(l["src"]):
            return COLOR_OUT, label
        dst = core.cell_by_id(st, l["dst"])
        if dst is not None and dst.get("speaker"):
            return COLOR_BYPASS, label
        return COLOR_DIRECT, label

    # ------------------------------------------------------------ moving
    def cell_moved(self, it: CellItem):
        view = self.box_widgets[it.box].view if it.box in self.box_widgets else None
        if view is not None:
            for li in view.links.values():
                ends = getattr(li, "ends", None)
                if ends and (ends[0] is it or ends[2] is it or ends[0].parentItem() is it or ends[2].parentItem() is it):
                    li.set_points(ends[0].port_scene_pos("out", ends[1]), ends[2].port_scene_pos("in", ends[3]))
        self.overlay.update()

    def cell_released(self, it: CellItem, global_pos):
        target = self.view_at(global_pos)
        if (target is not None and target.box != it.box and not it.hub and not it.ghost
                and self.outcome is None):
            cell = core.cell_by_id(self.c.state, it.cell_id)
            if cell is not None:
                sp = target.mapToScene(target.viewport().mapFromGlobal(global_pos))
                cell["box"] = target.box
                self.place_new_cell(it.cell_id, target.box, sp)
                self.c.commit()
                self.flush_pending_sync()
                return
        if not it.ghost:
            self.c.layout.set_pos(it.cell_id, it.pos().x(), it.pos().y())
        self.flush_pending_sync()

    def view_at(self, global_pos):
        w = QApplication.widgetAt(global_pos)
        while w is not None and not isinstance(w, BoxView):
            w = w.parentWidget()
        if w is None:     # widgetAt can't see under the cursor while a mouse grab is active: geometry
            for bw in self.visible_box_widgets():
                vp = bw.view.viewport()
                if QRect(vp.mapToGlobal(QPoint(0, 0)), vp.size()).contains(global_pos):
                    return bw.view
        return w

    # ------------------------------------------------------------ link drag
    def begin_link_drag(self, item, side, ch, global_pos):
        self.drag = {"item": item, "side": side, "ch": ch, "cursor": global_pos,
                     "start_pos": lambda: self._to_editor(item, item.port_scene_pos(side, ch)) or QPointF()}
        self.overlay.update()

    def update_link_drag(self, global_pos):
        if self.drag is not None:
            self.drag["cursor"] = global_pos
            self.overlay.update()

    def end_link_drag(self, global_pos):
        d, self.drag = self.drag, None
        self.overlay.update()
        if d is None:
            return
        target = self.port_at_global(global_pos, want_side="in" if d["side"] == "out" else "out")
        if target is not None:
            t_item, t_side, t_ch = target
            src, sch, dst, dch = (d["item"], d["ch"], t_item, t_ch) if d["side"] == "out" else (t_item, t_ch, d["item"], d["ch"])
            if src is not dst and not src.ghost and not dst.ghost:
                made = self.c.link_cells(src.cell_id, src.rows("out"), dst.cell_id, dst.rows("in"), sch, dch)
                if made:
                    self.c.commit()
        self.flush_pending_sync()

    def port_at_global(self, global_pos, want_side):
        view = self.view_at(global_pos)
        if view is None:
            return None
        vp = view.viewport().mapFromGlobal(global_pos)
        sp = view.mapToScene(vp)
        for item in view.scene_.items(sp):
            cell = item if isinstance(item, CellItem) else None
            if cell is None:
                continue
            hp = cell.port_at(cell.mapFromScene(sp))
            if hp is not None and hp[0] == want_side:
                return cell, hp[0], hp[1]
            if cell.body_rect().contains(cell.mapFromScene(sp)):
                rows = cell.rows(want_side)      # dropped on the body: Both if there is one
                if rows:
                    return cell, want_side, rows[0]
        return None

    # ------------------------------------------------------------ selection
    def is_cell_selected(self, cid):
        return cid in self.selected_cells

    def select_link(self, key, additive=False):
        if key.startswith("g:"):
            return
        if additive:
            self.selected ^= {key}
        else:
            self.selected = {key}
        self._repaint()

    def clear_selection(self):
        if self.selected:
            self.selected = set()
            self._repaint()

    def select_rect(self, view, rect: QRect, additive):
        found = set()
        sr = view.mapToScene(rect).boundingRect()
        for it in view.scene_.items(sr):
            if isinstance(it, LinkItem) and not it.ghost and not it.internal:
                if it.path.intersects(sr):
                    found.add(it.key)
        er = QRectF(QRect(view.viewport().mapTo(self, rect.topLeft()), rect.size()))
        for key, path, kind in self.cross_paths():
            if kind == "link" and path.intersects(er):
                found.add(key)
        self.selected = (self.selected | found) if additive else found
        self._repaint()

    def _repaint(self):
        for bw in self.box_widgets.values():
            bw.view.viewport().update()
        self.overlay.update()

    def delete_selected(self):
        if self.selected:
            core.remove_links(self.c.state, self.selected)
            self.selected = set()
            self.c.commit()

    # ------------------------------------------------------------ blocking
    def cell_middle_click(self, it: CellItem):
        if self.outcome is not None:
            if it.box == self.outcome_box and not it.hub and not it.ghost:
                cur = self.outcome["outcomes"].get(it.cell_id)
                nxt = {None: "block", "block": "unblock", "unblock": None}[cur]
                if nxt is None:
                    self.outcome["outcomes"].pop(it.cell_id, None)
                else:
                    self.outcome["outcomes"][it.cell_id] = nxt
                it.outcome = nxt
                it.update()
            return
        if it.hub or it.ghost:
            return
        cell = core.cell_by_id(self.c.state, it.cell_id)
        if cell is not None:
            cell["blocked"] = not cell.get("blocked", False)
            self.c.commit()

    def toggle_link_block(self, keys):
        links = [core.link_by_id(self.c.state, k) for k in keys]
        links = [l for l in links if l is not None]
        if not links:
            return
        target = not all(l.get("blocked") for l in links)
        for l in links:
            l["blocked"] = target
        self.c.commit()

    # ------------------------------------------------------------ menus
    def _exec_menu(self, menu, gpos):
        self._menus_open += 1
        try:
            menu.exec(gpos)
        finally:
            self._menus_open -= 1
            self.interacting = False
            QTimer.singleShot(0, self.flush_pending_sync)

    def cell_menu(self, it: CellItem, gpos):
        st = self.c.state
        menu = kit.themed_menu("", self)
        if it.ghost:
            t = menu.addAction(it.title)
            t.setEnabled(False)
            menu.addSeparator()
            menu.addAction("Add as Cell", lambda: self._adopt_ghost_cell(it))
            self._exec_menu(menu, gpos)
            return
        if it.hub:
            t = menu.addAction(core.hub_display_name(st, it.cell_id))
            t.setEnabled(False)
            menu.addSeparator()
            enabled = core.hub_enabled(st, it.cell_id)
            menu.addAction("Disable" if enabled else "Enable", lambda: self._toggle_box_enabled(it.box))
            if not it.is_ns and not it.cell_id.endswith(".mic"):
                menu.addAction("Master Volume…", lambda: self._hub_volume(it.box))
                hub = st[it.box] if it.box in ("speaker", "mic") else core.conduit_by_box(st, it.box)
                menu.addAction("Unmute" if hub.get("muted") else "Mute", lambda: self._hub_mute(it.box))
            if core.box_is_microphone(st, it.box):
                menu.addSeparator()
                cur = core.ns_method(st, it.box)
                for key, label in dlg.NOISE_SUPPRESSION_LABELS:
                    a = menu.addAction(f"Noise Suppression: {label}",
                                       lambda k=key: (core.set_noise_suppression(self.c.state, it.box, k),
                                                      QTimer.singleShot(0, self.c.commit)))
                    a.setCheckable(True)
                    a.setChecked(cur == key)
            self._exec_menu(menu, gpos)
            return
        cell = core.cell_by_id(st, it.cell_id)
        cid = it.cell_id
        t = menu.addAction(cell["label"])
        t.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Auto-Detect…" + ("  ✓" if dlg.auto_detect_active(cell["auto_detect"]) else ""),
                       lambda: self._auto_detect(cid, gpos))
        n = len(cell.get("events", []))
        menu.addAction("Add Event…" + (f"  ({n})" if n else ""), lambda: QTimer.singleShot(0, lambda: self.open_events(cid)))
        menu.addAction(f"Volume…  ({dlg.format_volume(cell['volume'])})", lambda: QTimer.singleShot(0, lambda: self._cell_volume(cid)))
        menu.addAction("Recolor…", lambda: QTimer.singleShot(0, lambda: self._cell_color(cid)))
        if cell.get("color"):
            menu.addAction("Reset Colour", lambda: self._set_cell(cid, color=None))
        menu.addAction("Unblock" if cell.get("blocked") else "Block", lambda: self._set_cell(cid, blocked=not cell.get("blocked")))
        sp = menu.addAction("Real Speaker (bypass target)")
        sp.setCheckable(True)
        sp.setChecked(cell.get("speaker", False))
        sp.triggered.connect(lambda on: self._set_cell(cid, speaker=on))
        move = menu.addMenu("Move to")
        move.setStyleSheet(menu.styleSheet())
        for box in core.all_boxes(st):
            if box != cell["box"]:
                move.addAction(core.box_label(st, box), lambda b=box: self._move_cell(cid, b))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self._delete_cell(cid))
        self._exec_menu(menu, gpos)

    def link_menu(self, key, gpos):
        st = self.c.state
        if key.startswith("g:"):
            g = self.ghost_links.get(key)
            if g is None:
                return
            menu = kit.themed_menu("", self)
            t = menu.addAction(f"{g['src_label']} → {g['dst_label']}  (not made by Conduit)")
            t.setEnabled(False)
            menu.addSeparator()
            menu.addAction("Add to Conduit", lambda: self._adopt_ghost_link(g, blocked=False))
            menu.addAction("Block (never link this pair)", lambda: self._adopt_ghost_link(g, blocked=True))
            self._exec_menu(menu, gpos)
            return
        keys = set(self.selected) if key in self.selected and len(self.selected) > 1 else {key}
        if key not in self.selected:
            self.selected = {key}
            self._repaint()
        links = [core.link_by_id(st, k) for k in keys]
        links = [l for l in links if l]
        if not links:
            return
        menu = kit.themed_menu("", self)
        if len(links) == 1:
            l = links[0]
            title = (f"{core.cell_label(st, l['src'])} {port_label(l['src_port'])} → "
                     f"{core.cell_label(st, l['dst'])} {port_label(l['dst_port'])}")
            if l.get("anti"):
                title += "  (automatic bypass anti-link)"
        else:
            title = f"{len(links)} links"
        t = menu.addAction(title)
        t.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Volume…", lambda: QTimer.singleShot(0, lambda: self._link_volume(keys)))
        menu.addAction("Recolor…", lambda: QTimer.singleShot(0, lambda: self._link_color(keys)))
        if any(l.get("color") for l in links):
            menu.addAction("Reset Colour", lambda: self._set_links(keys, color=None))
        blocked = all(l.get("blocked") for l in links)
        menu.addAction("Unblock" if blocked else "Block", lambda: self.toggle_link_block(keys))
        menu.addSeparator()
        menu.addAction("Delete", lambda: (core.remove_links(self.c.state, keys), self.selected.clear(), self.c.commit()))
        self._exec_menu(menu, gpos)

    def background_menu(self, view, scene_pos, gpos):
        st = self.c.state
        menu = kit.themed_menu("", self)
        menu.addAction("New Cell…", lambda: QTimer.singleShot(0, lambda: self.new_cell(view.box, scene_pos)))
        if view.box == "speaker":
            menu.addAction("New Speaker…", lambda: QTimer.singleShot(0, lambda: self.new_speaker(scene_pos)))
        menu.addSeparator()
        menu.addAction("Fit View", view.fit)
        menu.addAction("Reset Zoom", lambda: view.set_zoom(1.0))
        a = menu.addAction("Show Unmanaged Links")
        a.setCheckable(True)
        a.setChecked(bool(self.c.layout.ui("show_unmanaged")))
        a.triggered.connect(lambda on: self.set_show_unmanaged(on))
        if not core.box_enabled(st, view.box):
            menu.addSeparator()
            menu.addAction("Enable box", lambda: self._toggle_box_enabled(view.box))
        self._exec_menu(menu, gpos)

    def set_show_unmanaged(self, on):
        self.c.layout.set_ui("show_unmanaged", bool(on))
        self.show_unmanaged_changed.emit(bool(on))
        self.sync()

    # ------------------------------------------------------------ actions
    def _set_cell(self, cid, **kv):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is not None:
            cell.update(kv)
            QTimer.singleShot(0, self.c.commit)

    def _set_links(self, keys, **kv):
        for k in keys:
            l = core.link_by_id(self.c.state, k)
            if l is not None:
                l.update(kv)
        QTimer.singleShot(0, self.c.commit)

    def _move_cell(self, cid, box):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        cell["box"] = box
        self.c.layout.forget(cid)
        QTimer.singleShot(0, self.c.commit)

    def _delete_cell(self, cid):
        core.remove_cell(self.c.state, cid)
        self.c.layout.forget(cid)
        QTimer.singleShot(0, self.c.commit)

    def _toggle_box_enabled(self, box):
        st = self.c.state
        hub = st[box] if box in ("speaker", "mic") else core.conduit_by_box(st, box)
        if hub is not None:
            hub["enabled"] = not hub.get("enabled", True)
            QTimer.singleShot(0, self.c.commit)

    def _hub_volume(self, box):
        def go():
            st = self.c.state
            hub = st[box] if box in ("speaker", "mic") else core.conduit_by_box(st, box)
            v = dlg.ask_volume(self, f"{core.box_label(st, box)} — Master Volume", hub.get("volume", 1.0), unlimited=True)
            if v is not None:
                hub["volume"] = v
                self.c.commit()
        QTimer.singleShot(0, go)

    def _hub_mute(self, box):
        st = self.c.state
        hub = st[box] if box in ("speaker", "mic") else core.conduit_by_box(st, box)
        hub["muted"] = not hub.get("muted", False)
        QTimer.singleShot(0, self.c.commit)

    def _cell_volume(self, cid):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        v = dlg.ask_volume(self, f"{cell['label']} — Volume", cell.get("volume", 1.0))
        if v is not None:
            cell["volume"] = v
            self.c.commit()

    def _cell_color(self, cid):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        col = dlg.ask_color(self, cell.get("color"), f"{cell['label']} — Colour")
        if col:
            cell["color"] = col
            self.c.commit()

    def _link_volume(self, keys):
        links = [core.link_by_id(self.c.state, k) for k in keys]
        links = [l for l in links if l]
        if not links:
            return
        v = dlg.ask_volume(self, "Link Volume" if len(links) == 1 else f"Volume of {len(links)} links",
                           links[0].get("volume", 1.0))
        if v is not None:
            for l in links:
                l["volume"] = v
            self.c.commit()

    def _link_color(self, keys):
        links = [core.link_by_id(self.c.state, k) for k in keys]
        links = [l for l in links if l]
        if not links:
            return
        col = dlg.ask_color(self, links[0].get("color"), "Link Colour")
        if col:
            for l in links:
                l["color"] = col
            self.c.commit()

    def _auto_detect(self, cid, gpos):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        pop = dlg.AutoDetectPopup(cell["label"], cell["auto_detect"], self)

        def apply(cfg):
            c = core.cell_by_id(self.c.state, cid)
            if c is not None:
                c["auto_detect"] = cfg
                self.c.commit()
        pop.applied.connect(apply)
        dlg.show_popover_at(pop, gpos)

    def new_cell(self, box, scene_pos):
        st = self.c.state
        existing = {c["label"] for c in st["cells"] if c["box"] == box}
        d = dlg.DevicePickerDialog(f"New Cell — {core.box_label(st, box)}",
                                   dlg.device_entries(self.c.snap, self.c.layout, existing), self,
                                   "Apps, hardware and streams PipeWire has now, plus closed ones Conduit remembers.")
        if d.exec() and d.selected():
            cell = core.new_cell(st, d.selected(), box)
            self.place_new_cell(cell["id"], box, scene_pos)
            self.c.commit()

    def new_speaker(self, scene_pos):
        st = self.c.state
        existing = {c["label"] for c in st["cells"] if c["box"] == "speaker" and c.get("speaker")}
        d = dlg.DevicePickerDialog("New Speaker", dlg.device_entries(self.c.snap, self.c.layout, existing, True), self,
                                   "A real output device the Virtual Speaker plays to. Link an app straight to "
                                   "it to bypass the mix. Middle-click a speaker to block it.")
        if d.exec() and d.selected():
            cell = self.c.ensure_cell(d.selected(), "speaker", speaker=True)
            self.place_new_cell(cell["id"], "speaker", scene_pos)
            ports = self.c.cell_ports(cell["id"])
            self.c.link_cells(core.HUB_SPEAKER, ["*"] + core.STEREO, cell["id"],
                              (["*"] + ports["in"]) if len(ports["in"]) > 1 else ports["in"] or core.STEREO, "*", "*")
            self.c.commit()

    def _adopt_ghost_cell(self, it: CellItem):
        cell = self.c.ensure_cell(it.title, it.box)
        self.c.layout.set_pos(cell["id"], it.pos().x(), it.pos().y())
        QTimer.singleShot(0, self.c.commit)

    def _adopt_ghost_link(self, g, blocked):
        def real(cid, box, label):
            if not cid.startswith("ghost:"):
                return cid
            it = self.box_widgets[box].view.cells.get(cid)
            cell = self.c.ensure_cell(label, box)
            if it is not None:
                self.c.layout.set_pos(cell["id"], it.pos().x(), it.pos().y())
            return cell["id"]
        s = real(g["src"], g["src_box"], g["src_label"])
        d = real(g["dst"], g["dst_box"], g["dst_label"])
        core.new_link(self.c.state, s, g["src_port"], d, g["dst_port"], blocked=blocked)
        QTimer.singleShot(0, self.c.commit)

    # ------------------------------------------------------------ events
    def open_events(self, cid, events_override=None):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        tmp = dict(cell)
        if events_override is not None:
            tmp["events"] = events_override
        d = dlg.EventDialog(self.c.state, tmp, self)
        accepted = d.exec()
        idx = d.outcome_request()
        if idx is not None:
            self.start_outcome(cid, d.events, idx)
            return
        if accepted:
            cell = core.cell_by_id(self.c.state, cid)
            if cell is not None:
                cell["events"] = d.events
                self.c.commit()

    def start_outcome(self, cid, events, idx):
        cell = core.cell_by_id(self.c.state, cid)
        if cell is None:
            return
        box = cell["box"]
        if self.fullscreen_box not in (None, box):
            self.toggle_fullscreen(self.fullscreen_box)
        self.outcome = {"cell": cid, "events": events, "index": idx,
                        "outcomes": dict(events[idx].get("outcomes", {}))}
        self.outcome_box = box
        bw = self.box_widgets[box]
        bw.banner_text.setText(f"Outcome of “{cell['label']} — {core.EVENT_KIND_LABELS[events[idx]['kind']]}”: "
                               f"middle-click cells in this box — once blocks, twice unblocks, three times clears.")
        bw.banner.show()
        for it in bw.view.cells.values():
            it.outcome = self.outcome["outcomes"].get(it.cell_id)
            it.update()
        self._repaint()

    def finish_outcome(self, keep: bool):
        if self.outcome is None:
            return
        o, box = self.outcome, self.outcome_box
        self.outcome, self.outcome_box = None, None
        bw = self.box_widgets.get(box)
        if bw is not None:
            bw.banner.hide()
            for it in bw.view.cells.values():
                it.outcome = None
                it.update()
        events = o["events"]
        if keep:
            events[o["index"]]["outcomes"] = o["outcomes"]
        self._repaint()
        QTimer.singleShot(0, lambda: self.open_events(o["cell"], events))
