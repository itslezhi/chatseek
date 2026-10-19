const test=require('node:test');
const assert=require('node:assert/strict');
const {firstIncomplete,canAdvance}=require('../web/setup.js');

test('an empty installation starts at the environment check',()=>{
  assert.equal(firstIncomplete({}),0);assert.equal(canAdvance(0,{}),false);
});
test('later successes cannot skip a failed earlier step',()=>{
  const checks={environment:true,wechat:false,api:true,model:true};
  assert.equal(firstIncomplete(checks),1);assert.equal(canAdvance(2,checks),false);
  assert.equal(canAdvance(1,checks),false);assert.equal(canAdvance(0,checks),true);
});
test('completion requires every check, including a loaded local model',()=>{
  const checks={environment:true,wechat:true,api:true,model:false};
  assert.equal(firstIncomplete(checks),3);assert.equal(canAdvance(3,checks),false);
  checks.model=true;assert.equal(firstIncomplete(checks),4);assert.equal(canAdvance(3,checks),true);
});
