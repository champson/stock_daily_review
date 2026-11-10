export const dynamic='force-dynamic';
declare const __REVIEW_BUILD_INFO__: {
  schema_version:number;project_id:string;github_repository:string;commit_sha:string;
  tree_sha:string;dirty:boolean;source_url:string;built_at:string;
};
export async function GET(){
  return Response.json(__REVIEW_BUILD_INFO__,{headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
}
