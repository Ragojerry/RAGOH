/** A QR code drawn as a single SVG path (fast enough to animate on low-end phones). */
import qrcode from "qrcode-generator";
import { useMemo } from "react";
import Svg, { Path, Rect } from "react-native-svg";

export function QrCode({ value, size }: { value: string; size: number }) {
  const { d, n } = useMemo(() => {
    const qr = qrcode(0, "L");
    qr.addData(value, "Byte");
    qr.make();
    const count = qr.getModuleCount();
    let path = "";
    for (let r = 0; r < count; r++) {
      for (let c = 0; c < count; c++) if (qr.isDark(r, c)) path += `M${c + 4} ${r + 4}h1v1h-1z`;
    }
    return { d: path, n: count + 8 }; // 4-module quiet zone on each side
  }, [value]);
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${n} ${n}`}>
      <Rect x={0} y={0} width={n} height={n} fill="#ffffff" />
      <Path d={d} fill="#000000" />
    </Svg>
  );
}
