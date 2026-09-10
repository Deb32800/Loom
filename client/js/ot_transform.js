// JS port of server/ot_engine.py's transform() — must stay in sync with it.
// Ops are plain objects: {type:"insert",position,text} | {type:"delete",position,count} | {type:"noop"}

function makeNoop() {
    return { type: "noop" };
}

function transformInsertInsert(a, b) {
    if (a.position < b.position) {
        return [a, { type: "insert", position: b.position + a.text.length, text: b.text }];
    } else if (a.position > b.position) {
        return [{ type: "insert", position: a.position + b.text.length, text: a.text }, b];
    } else {
        return [a, { type: "insert", position: b.position + a.text.length, text: b.text }];
    }
}

function transformInsertDelete(ins, dlt) {
    if (ins.position <= dlt.position) {
        return [ins, { type: "delete", position: dlt.position + ins.text.length, count: dlt.count }];
    } else if (ins.position >= dlt.position + dlt.count) {
        return [{ type: "insert", position: ins.position - dlt.count, text: ins.text }, dlt];
    } else {
        return [makeNoop(), { type: "delete", position: dlt.position, count: dlt.count + ins.text.length }];
    }
}

function transformDeleteDelete(a, b) {
    const aEnd = a.position + a.count;
    const bEnd = b.position + b.count;
    if (aEnd <= b.position) {
        return [a, { type: "delete", position: b.position - a.count, count: b.count }];
    }
    if (bEnd <= a.position) {
        return [{ type: "delete", position: a.position - b.count, count: a.count }, b];
    }
    const overlapStart = Math.max(a.position, b.position);
    const overlapEnd = Math.min(aEnd, bEnd);
    const overlapCount = overlapEnd - overlapStart;
    const aPrimeCount = a.count - overlapCount;
    const bPrimeCount = b.count - overlapCount;
    let aPrimePos, bPrimePos;
    if (a.position <= b.position) {
        aPrimePos = a.position;
        bPrimePos = a.position;
    } else {
        aPrimePos = b.position;
        bPrimePos = b.position;
    }
    if (aPrimeCount === 0 && bPrimeCount === 0) return [makeNoop(), makeNoop()];
    if (aPrimeCount === 0) return [makeNoop(), { type: "delete", position: bPrimePos, count: bPrimeCount }];
    if (bPrimeCount === 0) return [{ type: "delete", position: aPrimePos, count: aPrimeCount }, makeNoop()];
    return [
        { type: "delete", position: aPrimePos, count: aPrimeCount },
        { type: "delete", position: bPrimePos, count: bPrimeCount },
    ];
}

// transform(a, b) -> [a', b'] such that apply(apply(doc,a),b') === apply(apply(doc,b),a')
function transform(a, b) {
    if (a.type === "noop" && b.type === "noop") return [makeNoop(), makeNoop()];
    if (a.type === "noop") return [makeNoop(), b];
    if (b.type === "noop") return [a, makeNoop()];
    if (a.type === "insert" && b.type === "insert") return transformInsertInsert(a, b);
    if (a.type === "insert" && b.type === "delete") return transformInsertDelete(a, b);
    if (a.type === "delete" && b.type === "insert") {
        const [bPrime, aPrime] = transformInsertDelete(b, a);
        return [aPrime, bPrime];
    }
    if (a.type === "delete" && b.type === "delete") return transformDeleteDelete(a, b);
    throw new Error(`Cannot transform ${a.type} vs ${b.type}`);
}
