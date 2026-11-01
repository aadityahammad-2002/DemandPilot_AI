export type Item = Record<string, any>
export type Page = {items:Item[];total:number;limit:number;offset:number}
export type Overview = {sku_count:number;store_count:number;total_units_sold:number;latest_inventory_units:number;inventory_as_of:string|null;stockout_observations:number;forecast_records:number;positive_recommendations:number;blocked_recommendations:number;last_successful_run:Item|null}
const BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/,'')
export class APIError extends Error {constructor(public status:number, message:string){super(message);this.name='APIError'}}
export async function request<T>(path:string, options:RequestInit={}):Promise<T>{
  const headers=new Headers(options.headers)
  headers.delete('Authorization')
  if(options.body&&typeof options.body==='string'&&!headers.has('Content-Type'))headers.set('Content-Type','application/json')
  let response:Response
  try{response=await fetch(BASE+path,{...options,headers})}
  catch(e){if(e instanceof TypeError)throw new APIError(0,'Local FastAPI is unavailable. Start the backend and refresh.');throw e}
  if(!response.ok){let message=`API request failed (${response.status})`
    try{const body=await response.json();const detail=body?.error?.message??body?.detail;if(typeof detail==='string')message=detail}catch{}
    throw new APIError(response.status,message)
  }
  return await response.json() as T
}
export function route(path:string, filters:Record<string,string|number|boolean|null|undefined>={}){
  const p=new URLSearchParams();Object.entries(filters).forEach(([k,v])=>{if(v!==null&&v!==undefined&&v!=='')p.set(k,String(v))})
  return '/api/v1/'+path+(p.size?'?'+p.toString():'')
}
export function fmtNumber(v:unknown){return v===null||v===undefined?'—':Number(v).toLocaleString('en-IN')}
export function fmtCurrency(v:unknown){return v===null||v===undefined?'—':Number(v).toLocaleString('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0})}
