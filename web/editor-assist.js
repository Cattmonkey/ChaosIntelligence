'use strict';
// Both editable surfaces use the same non-executing backend language service.
(() => {
  function boundary(text, position) {
    const before = text.charCodeAt(position - 1), after = text.charCodeAt(position);
    return !(before >= 0xD800 && before <= 0xDBFF && after >= 0xDC00 && after <= 0xDFFF);
  }

  function createSource({analyze, allowed, currentContext}) {
    if ([analyze, allowed, currentContext].some(callback => typeof callback !== 'function')) {
      throw new TypeError('Completion requires analyze, allowed and currentContext callbacks');
    }
    return async context => {
      try {
        if (context.aborted || !allowed() || (context.view?.composing || context.view?.compositionStarted)) return null;
        const scope = currentContext(), source = context.state.doc.toString(), position = context.pos;
        if (!Number.isInteger(position) || position < 0 || position > source.length || !boundary(source, position)) return null;
        const sameScope = () => {
          try { return allowed() && Object.is(scope, currentContext()); }
          catch { return false; }
        };
        const sameView = view => sameScope() && !view.composing && !view.compositionStarted &&
          view.state.doc.toString() === source && view.state.selection.main.head === position;

        // Empty prefixes and punctuation are valid requests. Only the backend
        // decides whether this is a member, module path, argument or plain text.
        const result = await analyze(position);
        if (context.aborted || !sameScope() || context.view && !sameView(context.view)) return null;
        if (!Array.isArray(result?.completions)) return null;
        const options = [];
        for (const item of result.completions) {
          if (!item || typeof item !== 'object') continue;
          const {from, to} = item;
          if (typeof item.label !== 'string' || !item.label ||
              !Number.isInteger(from) || !Number.isInteger(to) ||
              from < 0 || from > position || to < position || to > source.length ||
              !boundary(source, from) || !boundary(source, to)) continue;
          const insertion = typeof item.apply === 'string' ? item.apply : item.label;
          const option = {label: item.label};
          if (typeof item.type === 'string') option.type = item.type;
          if (typeof item.detail === 'string') option.detail = item.detail;
          option.apply = (view, completion) => {
            // A rendered menu can outlive a dependency change even without a
            // network race. Never apply its old edit to a changed workspace.
            if (!sameView(view)) return;
            const cm = window.ChaosCM;
            if (typeof cm?.insertCompletionText !== 'function' || typeof cm?.pickedCompletion?.of !== 'function') return;
            view.dispatch({
              ...cm.insertCompletionText(view.state, insertion, from, to),
              annotations: cm.pickedCompletion.of(completion)
            });
          };
          options.push({from, to, option});
        }
        if (!options.length) return null;
        // The service already filters by the caret prefix. Client fuzzy
        // filtering against the entire replacement token would hide valid
        // middle-of-token choices. Omitting validFor makes CodeMirror requery
        // on input instead of carrying a menu across '.', '/', quotes or '('.
        return {from: options[0].from, to: options[0].to, filter: false,
                options: options.map(item => item.option)};
      } catch {
        // Diagnostics remain the analysis surface's responsibility. Completion
        // failures must not interrupt typing or manufacture fallback symbols.
        return null;
      }
    };
  }

  window.ChaosEditorAssist = Object.freeze({createSource});
})();
