-- Phase 1 Step 7a — Seed subreddits.audience_prior
--
-- These are PRIORS, not labels. The cluster-level audience classifier (LLM,
-- per cluster) decides actual audience_type. Priors just inform the LLM and
-- raise its confidence when evidence agrees.
--
-- Methodology:
--   B2B   = professional/work context, target user is "doing their job"
--   B2C   = personal life, consumer apps, family/health/relationships
--   mixed = communities that genuinely host both (entrepreneur/founder forums)
--   unknown = everything else (default; left for LLM to decide)

BEGIN;

-- Reset everything to 'unknown' first (idempotent re-runs)
UPDATE subreddits SET audience_prior = 'unknown';

-- ─── B2B (professional / work context) ──────────────────────────────────────
UPDATE subreddits SET audience_prior = 'B2B' WHERE name IN (
    -- IT / Engineering / Tech
    'sysadmin', 'msp', 'devops', 'cybersecurity', 'networking',
    'ITmanager', 'cscareerquestions', 'experienceddevs', 'dataengineering',
    'AskEngineers', 'askengineers', 'engineering', 'mechanicalengineering',
    'industrialengineering', 'machinists', 'electricians', 'PLC', 'plc',
    'CAD', 'software', 'Analytics', 'cloudstorage', 'DataRecovery',
    -- Product / Project / Agile
    'ProductManagement', 'productmanagement', 'ProjectManagement', 'projectmanagement',
    'AgileManagement', 'scrum', 'InstructionalDesign', 'instructionaldesign',
    -- Sales / Marketing / Business dev
    'sales', 'SaaSSales', 'saassales', 'CustomerSuccess', 'customersuccess',
    'businessdevelopment', 'marketing', 'SEO', 'seo', 'bigseo', 'techseo', 'ppc',
    'socialmedia', 'recruiting', 'humanresources', 'askhr',
    'supportops', 'procurement', 'logistics', 'supplychain',
    -- Finance / Accounting / Legal
    'Accounting', 'accounting', 'Bookkeeping', 'bookkeeping', 'FPandA', 'fpanda',
    'CFP', 'cfp', 'TaxPros', 'taxpros', 'financialadvisors', 'financialcareers',
    'Compliance', 'compliance', 'GovCon', 'govcon',
    'Lawyers', 'LawFirm', 'lawfirm', 'paralegal', 'Ask_Lawyers', 'ask_lawyers',
    -- Healthcare / Education / Government
    'HealthcareAdmins', 'healthcareadmins', 'HospitalAdmins', 'hospitaladmins',
    'medicine', 'pharmacy', 'nursing',
    'Teachers', 'teachers', 'Professors', 'professors', 'HigherEd', 'highered',
    'specialed',
    -- Trades / Construction / Real Estate
    'Construction', 'ConstructionManagers', 'constructionmanagers', 'Contractor', 'contractor',
    'Plumbing', 'plumbing', 'estimators', 'Architecture', 'architecture',
    'CommercialRealEstate', 'commercialrealestate', 'PropertyManagement', 'propertymanagement',
    'realtors', 'Landlord', 'landlord', 'RealEstate',
    -- Logistics / Trucking / Manufacturing
    'Trucking', 'trucking', 'FreightBrokers', 'freightbrokers',
    'manufacturing', 'agriculture', 'farming', 'urbanfarming', 'macrogrowery',
    -- Hospitality / Service
    'KitchenConfidential', 'Restaurant_Managers', 'restaurant_managers', 'restaurateur',
    'hotelprofessionals',
    -- Ecommerce / shop ops
    'ecommerce', 'shopify', 'fulfillmentbyamazon', 'retail',
    'TalesFromRetail', 'TalesFromTheCustomer', 'TalesFromTheFrontDesk',
    'talesfromthecustomer', 'talesfromthefrontdesk'
);

-- ─── B2C (personal / consumer / family / health) ────────────────────────────
UPDATE subreddits SET audience_prior = 'B2C' WHERE name IN (
    -- Personal finance
    'personalfinance', 'povertyfinance', 'middleclassfinance',
    'Frugal', 'budget', 'ynab', 'mintuit', 'MonarchMoney', 'CopilotMoney',
    'Anticonsumption', 'subscriptions',
    -- Health / mental health
    'mentalhealth', 'Anxiety', 'bipolar', 'ADHD', 'AutismTranslated',
    'Fitness', 'LoseIt', 'MyFitnessPal', 'MacroFactor', 'AppleWatchFitness',
    -- Family / parenting / relationships
    'Parenting', 'Mommit', 'daddit', 'workingmoms', 'SingleParents',
    'coparenting', 'AgingParents', 'eldercare', 'CaregiverSupport', 'dementia',
    'relationships', 'dating_advice', 'OnlineDating', 'Tinder', 'SwipeHelper',
    'LongDistance',
    -- Travel
    'travel', 'solotravel', 'digitalnomad', 'TravelHacks', 'travelchina',
    'chinatravel', 'chinalife', 'Chinavisa', 'onebag',
    -- Smart home / consumer tech
    'HomeAssistant', 'HomeKit', 'homeautomation', 'smarthome', 'whatsapp',
    'discordapp', 'Bitwarden', 'degoogle', 'privacy',
    -- Productivity / personal organization (mostly consumer-facing)
    'Notion', 'ObsidianMD', 'Evernote', 'GoodNotes', 'Anki',
    'todoist', 'ticktick', 'gtd', 'productivity', 'digitaljournaling',
    'QuantifiedSelf',
    -- Lifestyle / hobby
    'languagelearning', 'GetStudying', 'college', 'Millennials',
    'photography', 'VideoEditing', 'DigitalArt', 'writing'
);

-- ─── Mixed (host both audiences regularly) ──────────────────────────────────
UPDATE subreddits SET audience_prior = 'mixed' WHERE name IN (
    'Entrepreneur', 'startup', 'smallbusiness', 'SaaS',
    'legaladvice',     -- mix of personal + business legal questions
    'technology',      -- consumer tech news + IT pros
    'email'            -- personal email tools + sysadmin email setup
);

-- Stats
DO $$
DECLARE
    n_b2b INT; n_b2c INT; n_mixed INT; n_unknown INT;
BEGIN
    SELECT COUNT(*) INTO n_b2b     FROM subreddits WHERE audience_prior='B2B';
    SELECT COUNT(*) INTO n_b2c     FROM subreddits WHERE audience_prior='B2C';
    SELECT COUNT(*) INTO n_mixed   FROM subreddits WHERE audience_prior='mixed';
    SELECT COUNT(*) INTO n_unknown FROM subreddits WHERE audience_prior='unknown';
    RAISE NOTICE 'audience_prior seed: B2B=%, B2C=%, mixed=%, unknown=%',
                 n_b2b, n_b2c, n_mixed, n_unknown;
END $$;

COMMIT;
