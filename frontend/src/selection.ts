/** Source run IDs (FC-RUN) and technical job IDs (JOB) have different contracts. */
export type RunCatalog={replenishment_runs:{job_id:string;run_id:string|null;forecast_run_id:string;recommendation_count:number;status?:string;completed_at?:string|null}[];forecast_runs:{run_id:string;job_id:string|null;from_date:string;to_date:string;forecast_count:number;has_recommendations:boolean;status?:"SUCCEEDED"|"IMPORTED";completed_at?:string|null}[];latest_forecast_run_id:string|null;latest_replenishment_forecast_run_id:string|null;coverage:Record<string,{from_date:string|null;to_date:string|null}>}
export function sourceRunId(value:string,catalog:RunCatalog|null):string{
  if(!value)return catalog?.latest_replenishment_forecast_run_id||catalog?.latest_forecast_run_id||''
  const matched=catalog?.forecast_runs.find(r=>r.run_id===value||r.job_id===value)
  return matched?.run_id||value
}

/** Pick the latest persisted purchasing job matching the selected forecast source. */
export function replenishmentJobId(selected:string,forecastSource:string,catalog:RunCatalog|null):string{
 if(selected)return selected
 return catalog?.replenishment_runs.find(r=>r.forecast_run_id===forecastSource)?.job_id||''
}
