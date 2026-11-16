/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,skill:string,active:boolean,version:number}} Crew */
/** @typedef {{id:number,name:string,start_at:number,end_at:number,active:boolean,version:number}} MaintenanceWindow */
/** @typedef {{id:number,job_id:number,band_index:number,title:string,from_km:number,to_km:number,work_hours:number,active:boolean}} MaintenanceTask */
/** @typedef {{id:number,name:string,status:string,items:Array,unscheduled:Array,invalidate_reason:string}} MaintenancePlan */
export {};
