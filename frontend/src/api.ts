export type Overview={vendors:number;models:number;prices:number;sources:string[];modalities:string[]};
export type Vendor={slug:string;name:string;zh:string;domain:string;models:number;prices:number;pricing_url:string};
export type Price={vendor:string;vendor_name:string;vendor_zh:string;model:string;modality:string;context_window:number|null;max_output:number|null;deployment_version:string|null;currency:string;billing_unit:string;service_tier:string;cache_state:string|null;context_range:string|null;input_price:number|null;output_price:number|null;cached_read:number|null;cached_write:number|null;source:string;source_url:string;time_window:Record<string,unknown>|null};
export type PriceResponse={total:number;items:Price[]};
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{const res=await fetch(`/api${path}`,{credentials:'include',headers:{'Content-Type':'application/json'},...options});if(!res.ok)throw new Error((await res.json().catch(()=>null))?.detail||res.statusText);return res.json() as Promise<T>}
export const getOverview=()=>api<Overview>('/overview');
export const getVendors=()=>api<Vendor[]>('/vendors');
export const getPrices=(params:Record<string,string|number>)=>api<PriceResponse>(`/prices?${new URLSearchParams(Object.entries(params).map(([k,v])=>[k,String(v)]))}`);
