import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { LabelLayout } from "echarts/features";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import type { ComponentProps } from "react";

echarts.use([
  ScatterChart,
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  LabelLayout,
  CanvasRenderer,
]);

type RuntimeProps = Omit<ComponentProps<typeof ReactEChartsCore>, "echarts"> & { echarts?: unknown };

export default function EChartsRuntime({ echarts: _ignored, ...props }: RuntimeProps) {
  return <ReactEChartsCore echarts={echarts} {...props} />;
}
