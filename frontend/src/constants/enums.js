export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const PlanStatus={draft:'试排中',confirmed:'已确认',invalidated:'已失效'};
export const UnscheduledReasonText={
 no_window:'没有可用天窗',window_too_short:'天窗短于登记工时（不拆工）',
 crew_busy:'班组时段已排满',mileage_busy:'重叠里程时段已占用',
 no_fit:'班组与里程双重占用，无可行落点',resource_missing:'缺少可用班组',
};
