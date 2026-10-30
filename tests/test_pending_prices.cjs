/* Exercise the tracking page's actual price display using server-summary fixtures. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('pending.html','utf8');
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))new vm.Script(match[1]);
const source=html.split('/* Payment price display start */')[1].split('/* Payment price display end */')[0];
for(const en of [false,true]){
    const nodes={};
    const context=vm.createContext({en,Intl,Number,document:{getElementById:id=>nodes[id]??={hidden:false,textContent:''}}});
    vm.runInContext(source,context);
    context.price={displayCurrency:'USD',displayTotal:'9.99',chargeCurrency:'EGP',chargeTotal:'546.50'};
    vm.runInContext('renderPricing(price)',context);
    assert.equal(nodes.paymentSummary.hidden,false);
    assert.equal(nodes.displayTotal.textContent,'9.99 USD');
    assert.match(nodes.chargeTotal.textContent,/546.50/);
    assert.equal(nodes.chargeTotal.hidden,false);
    assert.equal(nodes.bankNote.hidden,false);
    context.price={displayCurrency:'EGP',displayTotal:'500.00',chargeCurrency:'EGP',chargeTotal:'500.00'};
    vm.runInContext('renderPricing(price)',context);
    assert.equal(nodes.paymentSummary.hidden,false);
    assert.equal(nodes.chargeTotal.hidden,true);
    assert.equal(nodes.bankNote.hidden,true);
    for(const price of [null,{displayCurrency:'USD',displayTotal:'NaN',chargeCurrency:'EGP',chargeTotal:'100'},
                        {displayCurrency:'USD',displayTotal:'10',chargeCurrency:'USD',chargeTotal:'500'}]){
        context.price=price;vm.runInContext('renderPricing(price)',context);
        assert.equal(nodes.paymentSummary.hidden,true);
    }
}
console.log('Tracking price display: USD prominence, exact EGP disclosure, both languages and invalid-data handling passed.');
