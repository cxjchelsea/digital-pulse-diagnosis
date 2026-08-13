/** 纯展示用 SVG 折线坐标变换：不改变领域数值语义。 */

export function buildPolylinePoints(
  values: Array<number | null | string | undefined>,
  height: number,
  width = 100,
): {points: string; usable: boolean; readout: string} {
  const finiteValues: number[] = [];
  for (const item of values) {
    if (typeof item !== 'number' || !Number.isFinite(item)) {
      continue;
    }
    finiteValues.push(item);
  }

  if (finiteValues.length === 0) {
    return {points: '', usable: false, readout: '无可显示数值'};
  }

  if (finiteValues.length === 1) {
    const singleY = height / 2;
    const singleX = width / 2;
    return {
      points: `${singleX},${singleY} ${singleX + 0.01},${singleY}`,
      usable: true,
      readout: `单点 ${finiteValues[0]}`,
    };
  }

  const minValue = Math.min(...finiteValues);
  const maxValue = Math.max(...finiteValues);
  const span = maxValue - minValue || 1;
  const points = finiteValues
    .map((sample, index) => {
      const x = (index / (finiteValues.length - 1)) * width;
      const y = height - ((sample - minValue) / span) * height;
      return `${x},${y}`;
    })
    .join(' ');

  return {
    points,
    usable: true,
    readout: `n=${finiteValues.length} min=${minValue} max=${maxValue}`,
  };
}
