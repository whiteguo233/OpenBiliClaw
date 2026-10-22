// Lifecycle fixture only; real host restart evidence is recorded separately.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {workflowTaskSpace}=await import(pathToFileURL(process.argv[2]));
const original={id:1,name:'OpenBiliClaw-random',ownership:'agent',profileId:'Default'};
const config={task_id:1,task_name:original.name,profile:'Default',recover_missing_task:true};
let created=0,spaces=[],profiles=[{id:'Default'}];
const ego={listTaskSpaces:async()=>({taskSpaces:spaces}),listProfiles:async()=>({profiles}),
  createTaskSpace:async(name,profile)=>{created++;assert.equal(name,original.name);assert.equal(profile,'Default');return {id:2};}};
spaces=[original];assert.equal((await workflowTaskSpace(ego,config)).id,1);assert.equal(created,0);
spaces=[];assert.equal((await workflowTaskSpace(ego,config)).id,2);assert.equal(created,1);
await assert.rejects(workflowTaskSpace(ego,{...config,recover_missing_task:false}),/identity_changed/);
for(const collision of [{...original,ownership:'user'},{...original,name:'Renamed'},
  {...original,id:9},{...original,profileId:'Other'}]){
  spaces=[collision];await assert.rejects(workflowTaskSpace(ego,config),/identity_changed/);
}
assert.equal(created,1);spaces=[];profiles=[];
await assert.rejects(workflowTaskSpace(ego,config),/EGO_PROFILE_NOT_FOUND/);
assert.equal(created,1);console.log('owned task space restart recovery passed');
