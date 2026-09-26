"""Small software-rendered 3D buddy: articulated geometry, perspective and lighting."""
import math
from pathlib import Path
from functools import lru_cache
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPolygonF, QRadialGradient, QImage, QPainter, QTransform


def draw_avatar(p, width, height, phase, walking, breath, yaw, wave=False):
    phase_step = round((phase % (2*math.pi))*24/(2*math.pi)) % 24 if walking else 0
    breath_step = round((breath % (2*math.pi))*16/(2*math.pi)) % 16
    p.drawImage(0, 0, _frame(width, height, phase_step, walking, breath_step, round(yaw*12), wave))


@lru_cache(maxsize=128)
def _frame(width, height, phase_step, walking, breath_step, yaw_step, wave):
    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _render(painter, width, height, phase_step*2*math.pi/24, walking,
            breath_step*2*math.pi/16, yaw_step/12, wave)
    painter.end()
    return canvas


def _render(p, width, height, phase, walking, breath, yaw, wave):
    faces = []
    c, s = math.cos(yaw), math.sin(yaw)

    def transform(v):
        x, y, z = v
        return x*c + z*s, y, -x*s + z*c

    def project(v):
        x, y, z = v
        scale = 83 * 7 / (7-z)
        return QPointF(width/2+x*scale, height-24-y*scale)

    def ellipsoid(center, radii, color, tilt=0):
        vertices = []
        for j in range(13):
            lat = -math.pi/2 + math.pi*j/12
            row = []
            for i in range(24):
                lon = 2*math.pi*i/24
                x = radii[0]*math.cos(lat)*math.cos(lon)
                y = radii[1]*math.sin(lat)
                z = radii[2]*math.cos(lat)*math.sin(lon)
                y, z = y*math.cos(tilt)-z*math.sin(tilt), y*math.sin(tilt)+z*math.cos(tilt)
                row.append(transform((x+center[0], y+center[1], z+center[2])))
            vertices.append(row)
        for j in range(12):
            for i in range(24):
                vs = [vertices[j][i], vertices[j][(i+1)%24], vertices[j+1][(i+1)%24], vertices[j+1][i]]
                a = [vs[1][k]-vs[0][k] for k in range(3)]
                b = [vs[3][k]-vs[0][k] for k in range(3)]
                n = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
                length = math.sqrt(sum(t*t for t in n)) or 1
                # Mesh winding is inward; invert for lighting and backface removal.
                n = tuple(-t/length for t in n)
                if n[2] < -0.15:
                    continue
                light = .48 + .52*max(0, n[0]*-.4+n[1]*.6+n[2]*.7)
                shade = QColor(*(min(255, int(v*light)) for v in color))
                faces.append((sum(v[2] for v in vs)/4, QPolygonF([project(v) for v in vs]), shade))

    def limb(a, b, radius, color):
        center = tuple((a[i]+b[i])/2 for i in range(3))
        length = math.dist(a, b)
        tilt = math.atan2(-(b[2]-a[2]), -(b[1]-a[1]))
        ellipsoid(center, (radius, length/2+radius*.3, radius), color, tilt)

    suit, dark, shirt = (43, 49, 53), (24, 28, 31), (235, 229, 216)
    skin = (190, 132, 73)
    bob = abs(math.sin(phase))*.025 if walking else math.sin(breath)*.008

    def patch(points, color):
        vertices = [transform((v[0], v[1], v[2]+.085)) for v in points]
        faces.append((sum(v[2] for v in vertices)/len(vertices),
                      QPolygonF([project(v) for v in vertices]), QColor(*color)))

    for side in (-1, 1):
        swing = math.sin(phase+(0 if side == 1 else math.pi))*.48 if walking else 0
        hip = (side*.19, 1.19+bob, 0)
        knee = (side*.20, hip[1]-.48*math.cos(swing), .48*math.sin(swing))
        bend = max(0, -math.sin(phase+(0 if side == 1 else math.pi)))*.7 if walking else 0
        ankle = (side*.20, knee[1]-.46*math.cos(swing-bend), knee[2]+.46*math.sin(swing-bend))
        limb(hip, knee, .17, suit)
        ellipsoid(knee, (.15,.15,.15), suit)
        limb(knee, ankle, .135, suit)
        ellipsoid((ankle[0],ankle[1]-.035,ankle[2]+.12), (.16,.10,.28), (27,25,24))
        shoulder = (side*.39, 1.98+bob, 0)
        arm = -swing*.8
        elbow = (side*.46, shoulder[1]-.35*math.cos(arm), .35*math.sin(arm))
        hand = (side*.48, elbow[1]-.29, elbow[2]+.08)
        if wave and side == 1:
            elbow = (.57, 1.79+bob, .05)
            hand = (.78+math.sin(breath*2)*.035, 2.20+bob, .13)
        ellipsoid(shoulder, (.19,.20,.19), suit)
        limb(shoulder, elbow, .135, suit)
        ellipsoid(elbow, (.13,.13,.13), suit)
        wrist = tuple(elbow[i]+(hand[i]-elbow[i])*.86 for i in range(3))
        limb(elbow, wrist, .115, suit)
        limb(wrist, hand, .092, shirt)
        ellipsoid(hand, (.10,.14,.072), skin, -.3 if wave and side==1 else 0)
        if wave and side == 1:
            for finger in range(4):
                ellipsoid((hand[0]-.07+finger*.043,hand[1]+.14,hand[2]),
                          (.022,.10-abs(finger-1.5)*.014,.026), skin, -.25)
            ellipsoid((hand[0]-.115,hand[1]+.025,hand[2]),(.038,.08,.035),skin,.6)
    ellipsoid((0,1.23+bob,0), (.33,.24,.22), suit)
    ellipsoid((0,1.65+bob,0), (.39,.48,.235), suit)
    # White shirt, open collar, tailored lapels, belt and jacket buttons.
    patch([(-.19,2.05+bob,.20),(.19,2.05+bob,.20),(.16,1.23+bob,.235),(-.16,1.23+bob,.235)],shirt)
    for side in (-1,1):
        patch([(side*.20,2.09+bob,.22),(side*.34,1.94+bob,.23),
               (side*.24,1.80+bob,.265),(side*.29,1.72+bob,.26),
               (side*.08,1.42+bob,.27)], (31,36,39))
        patch([(side*.09,2.13+bob,.23),(side*.19,2.02+bob,.26),
               (side*.09,1.92+bob,.27),(0,2.03+bob,.27)], (246,241,229))
    patch([(-.28,1.29,.25),(.28,1.29,.25),(.28,1.20,.25),(-.28,1.20,.25)],(24,24,23))
    patch([(-.055,1.285,.263),(.055,1.285,.263),(.055,1.205,.263),(-.055,1.205,.263)],(110,111,104))
    patch([(-.035,1.27,.267),(.035,1.27,.267),(.035,1.22,.267),(-.035,1.22,.267)],dark)
    for y in (1.45,1.62,1.79,1.96):
        ellipsoid((.015,y+bob,.365),(.015,.018,.01),(114,108,96))
    for y in (1.40,1.61):
        ellipsoid((-.16,y+bob,.37),(.025,.026,.015),dark)
    patch([(.24,1.96+bob,.26),(.30,1.96+bob,.26),(.30,1.935+bob,.26),(.24,1.935+bob,.26)],(178,132,69))
    ellipsoid((0,2.12+bob,0),(.135,.20,.13),skin)
    shadow = QRadialGradient(QPointF(width/2,height-17), 65)
    shadow.setColorAt(0,QColor(12,20,38,75)); shadow.setColorAt(1,QColor(12,20,38,0))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(shadow)
    p.drawEllipse(QRectF(width/2-70,height-28,140,22))
    for _, polygon, color in sorted(faces, key=lambda f: f[0]):
        p.setPen(color); p.setBrush(color); p.drawPolygon(polygon)

    # The original face is retained as a texture on a turning head plane.
    # This keeps the user's likeness instead of substituting a generic robot face.
    head = _head_texture()
    corners = [(-.40,3.14+bob,.06),(.40,3.14+bob,.06),
               (.40,2.08+bob,.06),(-.40,2.08+bob,.06)]
    target = QPolygonF([project(transform(v)) for v in corners])
    source = QPolygonF([QPointF(0,0),QPointF(head.width(),0),
                       QPointF(head.width(),head.height()),QPointF(0,head.height())])
    mapping = QTransform()
    if QTransform.quadToQuad(source,target,mapping):
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setTransform(mapping,True)
        p.drawImage(0,0,head)
        p.restore()


@lru_cache(maxsize=1)
def _head_texture():
    original = QImage(str(Path(__file__).parent / "sprite.png"))
    return original.copy(83, 0, 146, 195)
