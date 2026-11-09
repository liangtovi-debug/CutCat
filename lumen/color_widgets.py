"""Native color gestures stage locally and commit once on release."""
import math
import copy
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from .i18n_widgets import QWidget, QLabel, QDoubleSpinBox
from .i18n import tr, locale_events

class ColorWheel(QWidget):
    committed = Signal(object)
    def __init__(self, low=-1., high=1., parent=None):
        super().__init__(parent)
        self.low, self.high = low, high
        self.values = [0., 0., 0.]; self.dragging = False
        self.setMinimumSize(116,116); self.setMaximumSize(150,150)
        self.setToolTip(tr('Drag RGB color bias; release commits one undo step. RGB fields provide exact values.'))
        self.disc = QImage(120,120,QImage.Format.Format_ARGB32); self.disc.fill(Qt.GlobalColor.transparent)
        for y in range(120):
            for x in range(120):
                dx,dy=(x-59.5)/59,(59.5-y)/59; radius=math.hypot(dx,dy)
                if radius<=1: self.disc.setPixelColor(x,y,QColor.fromHsvF((math.atan2(dy,dx)/(2*math.pi))%1,radius,.78))
    def set_values(self,values): self.values=list(values); self.update()
    def geometry_values(self): return QPointF(self.width()/2,self.height()/2), min(self.width(),self.height())/2-8
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); center,radius=self.geometry_values()
        p.drawImage(QRectF(center.x()-radius,center.y()-radius,radius*2,radius*2),self.disc)
        p.setPen(QPen(QColor('#91a0ad'),1)); p.drawLine(center-QPointF(5,0),center+QPointF(5,0)); p.drawLine(center-QPointF(0,5),center+QPointF(0,5))
        r,g,b=self.values; x,y=(2*r-g-b)/3,(g-b)/math.sqrt(3); length=max(1.,math.hypot(x,y)/.5)
        marker=center+QPointF(x/.5/length*radius,-y/.5/length*radius)
        p.setPen(QPen(QColor('white'),2)); p.setBrush(QColor('#20262f')); p.drawEllipse(marker,5,5)
    def _stage(self,position):
        center,radius=self.geometry_values(); x,y=(position.x()-center.x())/radius,(center.y()-position.y())/radius
        length=max(1.,math.hypot(x,y)); x/=length; y/=length
        self.values=[max(self.low,min(self.high,self.mean+.5*v)) for v in (x,-.5*x+math.sqrt(3)/2*y,-.5*x-math.sqrt(3)/2*y)]; self.update()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.dragging=True; self.before=self.values[:]; self.mean=sum(self.values)/3; self._stage(event.position())
    def mouseMoveEvent(self,event):
        if self.dragging: self._stage(event.position())
    def mouseReleaseEvent(self,event):
        if self.dragging:
            self._stage(event.position()); self.dragging=False
            if self.values!=self.before: self.committed.emit(self.values[:])

class WheelControl(QWidget):
    committed=Signal(object)
    def __init__(self,name,low,high,parent=None):
        super().__init__(parent); outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.addWidget(QLabel(name))
        row=QHBoxLayout(); self.wheel=ColorWheel(low,high); row.addWidget(self.wheel); fields=QVBoxLayout(); self.spins=[]
        for name,color in zip('RGB',('#ef7979','#79d5a1','#7aa9ed')):
            line=QHBoxLayout(); label=QLabel(name); label.setStyleSheet('color:'+color); line.addWidget(label)
            spin=QDoubleSpinBox(); spin.setRange(low,high); spin.setDecimals(3); spin.setSingleStep(.025); spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._numeric); line.addWidget(spin); fields.addLayout(line); self.spins.append(spin)
        row.addLayout(fields); outer.addLayout(row); self.wheel.committed.connect(self._wheel)
    def set_values(self,values):
        self.wheel.set_values(values)
        for spin,value in zip(self.spins,values): spin.blockSignals(True); spin.setValue(value); spin.blockSignals(False)
    def _numeric(self):
        values=[s.value() for s in self.spins]; self.wheel.set_values(values); self.committed.emit(values)
    def _wheel(self,values): self.set_values(values); self.committed.emit(values)

class CurveEditor(QWidget):
    committed=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent); self.points=[[0.,0.],[1.,1.]]; self.selected=None; self.dragging=False
        locale_events.changed.connect(self.update)
        self.setMinimumSize(230,220); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(tr('Drag points. Double-click adds; right-click or Delete removes interior points. Endpoints stay at x=0/1. Output range −1 to 2.'))
    def set_points(self,points):
        self.dragging=False; self.points=copy.deepcopy(points); self.selected=None; self.update()
    def cancel_gesture(self):
        if self.dragging:
            self.points=copy.deepcopy(self.before); self.dragging=False; self.update()
    def plot_rect(self): return QRectF(30,12,self.width()-45,self.height()-40)
    def to_screen(self,point):
        r=self.plot_rect(); return QPointF(r.left()+point[0]*r.width(),r.bottom()-(point[1]+1)/3*r.height())
    def from_screen(self,p):
        r=self.plot_rect(); return [max(0.,min(1.,(p.x()-r.left())/r.width())),max(-1.,min(2.,(r.bottom()-p.y())/r.height()*3-1))]
    def nearest(self,pos):
        distances=[math.hypot((self.to_screen(p)-pos).x(),(self.to_screen(p)-pos).y()) for p in self.points]; index=min(range(len(distances)),key=distances.__getitem__)
        return index if distances[index]<=12 else None
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); r=self.plot_rect(); p.fillRect(r,QColor('#111821')); p.setPen(QPen(QColor('#34424e'),1))
        for y in (-1,0,.5,1,2):
            sy=self.to_screen([0,y]).y(); p.drawLine(QPointF(r.left(),sy),QPointF(r.right(),sy)); p.drawText(2,int(sy+4),str(y))
        for x in (0,.25,.5,.75,1):
            sx=self.to_screen([x,0]).x(); p.drawLine(QPointF(sx,r.top()),QPointF(sx,r.bottom()))
        p.setPen(QPen(QColor('#627083'),1,Qt.PenStyle.DashLine)); p.drawLine(self.to_screen([0,0]),self.to_screen([1,1]))
        path=QPainterPath(self.to_screen(self.points[0]))
        for point in self.points[1:]: path.lineTo(self.to_screen(point))
        p.setPen(QPen(QColor('#7cddc4'),2)); p.drawPath(path)
        for i,point in enumerate(self.points): p.setBrush(QColor('#f3be73' if i==self.selected else '#dce8ef')); p.drawEllipse(self.to_screen(point),4,4)
        p.setPen(QColor('#9aabba')); p.drawText(30,self.height()-5,tr('Input 0 → 1  ·  output −1 → 2'))
    def add_point(self,x,y):
        if len(self.points)>=32 or not 0<x<1 or any(abs(x-p[0])<1e-5 for p in self.points): return False
        self.points.append([float(x),max(-1.,min(2.,float(y)))]); self.points.sort(key=lambda p:p[0]); self.update(); self.committed.emit(copy.deepcopy(self.points)); return True
    def remove_point(self,index):
        if index is None or index<=0 or index>=len(self.points)-1: return False
        self.points.pop(index); self.selected=None; self.update(); self.committed.emit(copy.deepcopy(self.points)); return True
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.nearest(event.position()) is None: self.add_point(*self.from_screen(event.position()))
    def mousePressEvent(self,event):
        self.setFocus(); self.selected=self.nearest(event.position()); self.update()
        if event.button()==Qt.MouseButton.RightButton: self.remove_point(self.selected)
        elif event.button()==Qt.MouseButton.LeftButton and self.selected is not None: self.dragging=True; self.before=copy.deepcopy(self.points); self.press_position=event.position(); self.moved=False
    def mouseMoveEvent(self,event):
        if self.dragging and self.selected is not None:
            if not self.moved and (event.position()-self.press_position).manhattanLength()<2: return
            self.moved=True
            x,y=self.from_screen(event.position()); i=self.selected
            if i==0: x=0.
            elif i==len(self.points)-1: x=1.
            else: x=max(self.points[i-1][0]+1e-6,min(self.points[i+1][0]-1e-6,x))
            self.points[i]=[x,y]; self.update()
    def mouseReleaseEvent(self,event):
        if self.dragging:
            self.mouseMoveEvent(event); self.dragging=False
            if self.points!=self.before:
                selected=self.selected; self.committed.emit(copy.deepcopy(self.points)); self.selected=selected; self.update()
    def event(self,event):
        if event.type()==QEvent.Type.ShortcutOverride and event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace,Qt.Key.Key_Escape):
            event.accept(); return True
        if event.type() in (QEvent.Type.FocusOut,QEvent.Type.WindowDeactivate,QEvent.Type.Hide): self.cancel_gesture()
        return super().event(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape: self.cancel_gesture()
        elif event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace): self.remove_point(self.selected)
        else: super().keyPressEvent(event)
