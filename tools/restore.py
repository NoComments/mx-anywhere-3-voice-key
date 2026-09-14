"""
restore.py -- clear any leftover diversion, putting the buttons back to normal.

If mxvoice.py is killed abruptly instead of stopped with Ctrl+C, the device can
be left with a control still diverted. For 0xC4 that means the wheel mode-shift
button stops performing its normal firmware action. Run this to undo it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hidpp            # noqa: E402
import mxvoice as mv    # noqa: E402

# Every control the tools in this folder may have touched.
CIDS = (0x0052, 0x0053, 0x0056, 0x00C3, 0x00C4, 0x00D7)


def main():
    h, path, caps, hr, hq = mv.find_mouse_handle()
    print(f"device: ...{path[-56:]}")
    dev = mv.HidppDevice(h, 0, hr, None, hq)
    dev.feat = dev.get_feature_index(mv.FEAT_REPROG_CONTROLS_V4)
    print(f"0x1b04 at feature index {dev.feat}")

    touched = 0
    for cid in CIDS:
        try:
            before = dev.get_cid_reporting(cid)
        except Exception as e:
            print(f"  0x{cid:04x}: 读取失败 {e}")
            continue
        busy = (before["diverted"] or before["analytics"] or before["raw_xy"]
                or before["force_raw_xy"] or before["raw_wheel"])
        if not busy:
            print(f"  0x{cid:04x}: 正常")
            continue
        try:
            dev.set_cid_reporting(cid, diverted=False, analytics=False,
                                  raw_xy=False, force_raw_xy=False,
                                  raw_wheel=False)
            after = dev.get_cid_reporting(cid)
            state = "已还原" if not after["diverted"] else "还原失败"
            print(f"  0x{cid:04x}: 被占用 -> {state}")
            touched += 1
        except Exception as e:
            print(f"  0x{cid:04x}: 清除失败 {e}")

    for x in {h, hr, hq}:
        hidpp.close(x)
    print(f"\n完成，处理了 {touched} 个被占用的控件。")


if __name__ == "__main__":
    sys.exit(main())
