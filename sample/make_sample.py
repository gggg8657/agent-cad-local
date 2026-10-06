"""데모용 건축 DXF 생성: python sample/make_sample.py → sample/station_B2.dxf
지하 승강장(섬식) + 통신기기실 + 계단 + 기존 CCTV 6대(타사 블록명 'CAM_DOME', 속성 TAG). 실제 도면 대신 쓰는 합성 예제."""
import os
import sys

import ezdxf

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_B2.dxf")


def main(out=OUT):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.MM
    for n, c in (("A-WALL", 8), ("A-ROOM", 7), ("A-STAIR", 9), ("E-CCTV", 1)):
        doc.layers.add(n, color=c)
    msp = doc.modelspace()

    def room(x, y, w, h, label, layer="A-WALL"):
        msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, dxfattribs={"layer": layer})
        msp.add_text(label, height=600, dxfattribs={"layer": "A-ROOM"}).set_placement((x + w / 2, y + h / 2), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    # 외곽 박스 (역사 전체)
    msp.add_lwpolyline([(-2000, -8000), (150000, -8000), (150000, 26000), (-2000, 26000)], close=True, dxfattribs={"layer": "A-WALL"})
    room(0, 0, 140000, 12000, "승강장")
    room(5000, 14000, 9000, 7000, "통신기기실")
    room(16000, 14000, 9000, 7000, "신호기계실")
    room(60000, 14000, 20000, 8000, "역무실")
    room(30000, 3000, 6000, 6000, "계단1", "A-STAIR")
    room(104000, 3000, 6000, 6000, "계단2", "A-STAIR")
    room(0, -6000, 140000, 4000, "선로(하선)", "A-WALL")
    blk = doc.blocks.new("CAM_DOME")
    blk.add_circle((0, 0), 250)
    blk.add_line((-250, 0), (250, 0))
    blk.add_attdef("TAG", (0, -450), dxfattribs={"height": 200})
    for i in range(6):
        ins = msp.add_blockref("CAM_DOME", (12000 + i * 22000, 9000), dxfattribs={"layer": "E-CCTV"})
        ins.add_auto_attribs({"TAG": f"CAM-{i + 1:02d}"})
    doc.saveas(out)
    return out


if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else OUT))
