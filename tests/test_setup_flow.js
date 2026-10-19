const test=require('node:test');
const assert=require('node:assert/strict');
const {firstIncomplete,canAdvance,accountView}=require('../web/setup.js');

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
test('one discovered data folder requires confirmation and has no decorative dropdown',()=>{
  const view=accountView({state:'choose_account',detected_count:1,selected:null,can_change_account:false});
  assert.equal(view.chooser,true);assert.equal(view.dropdown,false);
  assert.equal(view.initialize,false);assert.equal(view.verify,false);
});
test('multiple data folders show a working choice before connection',()=>{
  const view=accountView({state:'choose_account',detected_count:2,selected:null,can_change_account:false});
  assert.equal(view.chooser,true);assert.equal(view.dropdown,true);
});
test('an existing connection uses a card and verification instead of disabled choice controls',()=>{
  const view=accountView({state:'saved_connection',detected_count:1,selected:{id:'saved'},can_change_account:false});
  assert.equal(view.selected,true);assert.equal(view.verify,true);
  assert.equal(view.chooser,false);assert.equal(view.dropdown,false);assert.equal(view.initialize,false);
});
test('a verified account only needs the next step and does not offer another read check',()=>{
  const view=accountView({state:'verified',detected_count:1,selected:{id:'saved'},can_change_account:false});
  assert.equal(view.selected,true);assert.equal(view.verify,false);assert.equal(view.initialize,false);
  assert.equal(canAdvance(1,{environment:true,wechat:true}),true);
});
test('a confirmed first-time account offers initialization and can be reselected',()=>{
  const connection={state:'initialize',detected_count:2,selected:{id:'saved'},can_change_account:true};
  assert.equal(accountView(connection).initialize,true);assert.equal(accountView(connection).change,true);
  assert.equal(accountView(connection,true).chooser,true);assert.equal(accountView(connection,true).initialize,false);
});
test('an empty installation and incomplete saved credentials cannot masquerade as connected',()=>{
  const empty=accountView({state:'no_data',detected_count:0,selected:null});
  assert.equal(empty.noData,true);assert.equal(empty.verify,false);
  const broken=accountView({state:'needs_attention',detected_count:1,selected:null,problem:'missing account'});
  assert.equal(broken.verify,false);assert.equal(broken.chooser,false);
});
