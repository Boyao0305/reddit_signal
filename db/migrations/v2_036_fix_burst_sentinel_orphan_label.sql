-- v2_036: Fix corpus burst sentinel refresh function.
--
-- v2_034 computes orphan_label in the `lbl` lateral subquery but selected
-- `cb.orphan_label`, which does not exist. Rebuild the existing function with
-- the corrected reference while preserving the rest of the deployed definition.

DO $$
DECLARE
    v_fn TEXT;
BEGIN
    SELECT pg_get_functiondef(
        'refresh_dashboard_market_opportunities(integer, integer, text)'::regprocedure
    )
    INTO v_fn;

    IF v_fn IS NULL THEN
        RAISE EXCEPTION 'refresh_dashboard_market_opportunities(integer, integer, text) not found';
    END IF;

    IF POSITION('cb.orphan_label,' IN v_fn) > 0 THEN
        v_fn := REPLACE(v_fn, 'cb.orphan_label,', 'lbl.orphan_label,');
        EXECUTE v_fn;
    ELSE
        RAISE NOTICE 'refresh_dashboard_market_opportunities already uses corrected orphan_label reference';
    END IF;
END;
$$;
