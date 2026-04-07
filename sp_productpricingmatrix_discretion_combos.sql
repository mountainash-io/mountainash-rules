
--===========================================
--	View:		sp_productpricingmatrix_discretion_combos
--	Schema:		pmx
--	Purpose:	Returns the final combination of valid rules in a ruleset for Discretionary Margins
--  Author:		Nathaniel Ramm
--	Date:		2016-10-26
--	Notes:		
--	Dependencies:	v_productpricingmatrix_discretion
--
-- Notes:   THERE IS A *LOT* GOING ON IN THIS QUERY...
--
-- ====== WHAT THIS QUERY DOES ========
-- From v_productpricingmatrix_tier we have a list of margin cells - rules. These represent the fundamental building blocks of pricing margins.
-- We recursively join this list to itself to build ALL VALID COMBINATIONS OF RULES FOR A PRODUCT, given the attributes of each rule.
-- We then filter based upon whether the ruleset is the final SUPERSET, as each iteration generates a record

-- ====== HOW THIS QUERY WORKS ========
-- RECURSIVE JOIN
-- Firstly, the recursive nature of this query uses a Common Table Expression (CTE).
-- This CTE defines a root table (labelled a, and referred to hereon as the LHS of the join) and performs a UNION ALL with a similar table (b, RHS), 
-- and joins back onto the root (LHS).
-- With each iteration, the previous RHS becomes the new LHS, so we progressively build up rulesets, rule by rule.

-- RULE MATCHING CRITERIA:
-- The criteria for the join is whether the LHS and RHS rules agree, given a three-valued logic. (Yes, No, Don't Care)
-- Each iteration in building up a ruleset must remember the combined attributes of all previously joined rules.
-- The iterative nature of this requires a coalescing of all previous attributes with the new rule to be joined. 
-- This coalescing favours HARD ATTRIBUTES (ie: an actual reference ID for a rule attribute), over DON'T CARE attributes, and progressively builds up the DNA of the ruleset.
-- Therefore each iteration has a memory of past iterations, via iterative coalescing.

-- RULESET DEFINITION
-- A ruleset is a unique combination of rule attributes. We use the 'NON-BANDED' ruleset to manage statespace for filtering rules.
-- However, there are some criteria for this:
-- 1. For Product-based attributes, we use the known attrributes from the product_id & loanpurpose. These are known for all discretion rules in advance, based on the join to indicator rates and tiers.
-- 2. For Non-banded, Non-product attributes we use the attributes from the discretion rule. Different values here will create different rulesets, and carve out a namespace for deetermining whether a superset exists.
-- 3. BANDED variables are not included in the ruleset definition, as we do not want the various bands to affect the ruleset namespacing.
--    Rulesets that have a banding rule that matches will use only the BA

-- FILTERING CRITERIA
-- We only need to keep the final ruleset matching each criteria, threfore we need to filter out 'subset' rulesets - those rows that were an intermediate step in building the final ruleset.
-- This is done through assigning each margin cell (or rule) a PRIME NUMBER, and multiplying each rule's prime value by the product of all previous rules.
-- This gives us a 'PRODUCT OF PRIMES' for each ruleset.
-- To determine whether a rule is the Superset of other rules, we then compare each ruleset and using prime factorisation determine whether a rule is a subset of another.
-- This works through testing whether the quotient of the two product-of-primes is an integer. If it is an integer, we have a subset/superset relationship. 
-- We keep only the supersets.

-- TABLE VALUES FUNCTION
-- This query is too complex for the SQL Optimiser.
-- I had to create table valued functions in order to force the materialising of the discretion rules, and their combinations...

--===========================================

-- select * from  pmx.sp_productpricingmatrix_discretion_combos()
-- drop function pmx.sp_productpricingmatrix_discretion_combos



create function pmx.sp_productpricingmatrix_discretion_combos(
	@floor_type nvarchar(20)
)

RETURNS @t TABLE(


		authoritylevel_id int
		, authoritylevelorder int 
		, authoritylevelname [nvarchar](20)
		
		-- === LHS Indicator Rate ===
		,indicatorrate [decimal](18, 4)


		-- === LHS Product Attributes ===
		--probably need to include all product structurals - and use as basis foppr the base rule 'co' values
		,product_id int
		,loanpurpose_id int
		--,loanamountband_id
		,productterms_id int
		,productgroup_id int
		,packagetype_id int
		,interestterms_id int
		,interesttiming_id int
		,repaymenttype_id int
		,contracttype_id int
		,interestterms_fixed_id int


		-- === LHS Discretion IDs ===
		,disc_product_id int
		,disc_loanpurpose_id int

		,disc_productterms_id int
		,disc_productgroup_id int
		,disc_packagetype_id int
		,disc_interestterms_id int
		,disc_interesttiming_id int
		,disc_repaymenttype_id int
		,disc_contracttype_id int
		,disc_interestterms_fixed_id int
		,disc_channel_id int
		,disc_segmentgroup_id int
		,disc_securitylocationgroup_id int
		,disc_bankerbuidgroup_id int
		,disc_competitorgroup_id int
		,disc_cust_foreignresident_id int
		,disc_cust_staff_id int
		,disc_requesttype_id int
		,disc_requesttypegroup_id int
		,disc_introducercommission_id int

		,disc_cust_lvrband_id int
		,disc_cust_agglimitband_id int
		,disc_cust_netutilband_id int
		,disc_cust_riskweightband_id int
		,disc_randomisedcontrolgroup_id int

		,disc_cust_lvrband_system_id int
		,disc_cust_agglimitband_system_id int
		,disc_cust_netutilband_system_id int
		,disc_cust_riskweightband_system_id int
		,disc_randomisedcontrolgroup_system_id int
		 

		-- === LHS NA Flags ===
		,product_naflag int
		,loanpurpose_naflag int
		
		,productterms_naflag int
		,productgroup_naflag int
		,packagetype_naflag int
		,interestterms_naflag int
		,interesttiming_naflag int
		,repaymenttype_naflag int
		,contracttype_naflag int
		,interestterms_fixed_naflag int
		,channel_naflag int

		,segmentgroup_naflag int
		,securitylocationgroup_naflag int
		,bankerbuidgroup_naflag int
		,competitorgroup_naflag int
		,cust_foreignresident_naflag int
		,cust_staff_naflag int
		,requesttype_naflag int
		,requesttypegroup_naflag int
		,introducercommission_naflag int

		,cust_lvrband_naflag int
		,cust_agglimitband_naflag int
		,cust_netutilband_naflag int
		,cust_riskweightband_naflag int
		,randomisedcontrolgroup_naflag int


		-- === LHS NA Flags Coalesced ===
		,co_product_naflag int
		,co_loanpurpose_naflag int

		,co_productterms_naflag int
		,co_productgroup_naflag int
		,co_packagetype_naflag int
		,co_interestterms_naflag int
		,co_interesttiming_naflag int
		,co_repaymenttype_naflag int
		,co_contracttype_naflag int
		,co_interestterms_fixed_naflag int

		,co_channel_naflag int

		,co_segmentgroup_naflag int
		,co_securitylocationgroup_naflag int
		,co_bankerbuidgroup_naflag int
		,co_competitorgroup_naflag int
		,co_cust_foreignresident_naflag int
		,co_cust_staff_naflag int
		,co_requesttype_naflag int
		,co_requesttypegroup_naflag int
		,co_introducercommission_naflag int

		,co_cust_lvrband_naflag int
		,co_cust_agglimitband_naflag int
		,co_cust_netutilband_naflag int
		,co_cust_riskweightband_naflag int
		,co_randomisedcontrolgroup_naflag int



		-- === LHS Coalesced Discretion Flags - Non Banded ===
		,co_disc_product_id int
		,co_disc_loanpurpose_id int
						
		,co_disc_productgroup_id int
		,co_disc_packagetype_id int
		,co_disc_productterms_id int
		,co_disc_interestterms_id int
		,co_disc_interesttiming_id int
		,co_disc_repaymenttype_id int
		,co_disc_contracttype_id int
		,co_disc_interestterms_fixed_id int
		
		,co_disc_channel_id int
		,co_disc_segmentgroup_id int
		,co_disc_securitylocationgroup_id int
		,co_disc_competitorgroup_id int
		,co_disc_bankerbuidgroup_id int
		
		,co_disc_cust_foreignresident_id int
		,co_disc_cust_staff_id int
		,co_disc_requesttype_id int
		,co_disc_requesttypegroup_id int
		,co_disc_introducercommission_id int


		-- === LHS HASHED AND Coalesced Discretion Flags - Non Banded ===
		, ruleset_nonbanded nvarchar(40)

		-- === LHS Coalesced Discretion Flags - Banded ===
		,co_disc_cust_lvrband_id int
		,co_disc_cust_agglimitband_id int
		,co_disc_cust_netutilband_id int
		,co_disc_cust_riskweightband_id int
		,co_disc_randomisedcontrolgroup_id int

		-- === LHS HASHED AND Coalesced Discretion Flags - Banded ===
		, ruleset_banded nvarchar(40)

		-- === LHS Coalesced Discretion Flags - Banding System ===
		,co_disc_cust_lvrband_system_id int
		,co_disc_cust_agglimitband_system_id int
		,co_disc_cust_netutilband_system_id int
		,co_disc_cust_riskweightband_system_id int
		,co_disc_randomisedcontrolgroup_system_id int

		-- === LHS HASHED AND Coalesced Discretion Flags - Banding System ===
		,ruleset_banding_system nvarchar(40)

		-- === LHS Coalesced Banding NA Flags  ===
 
		, rule_num_disc_bandings int

		-- === LHS Margins  ===
		,margin_value float
		,aggregate_margin float

		,margin_value_desk float
		,aggregate_margin_desk float

		-- === LHS Recursion Control Fields  ===
		, [level] int
		, combination VARCHAR(80) 
		, combination_shape VARCHAR(80)
		

		, combination_primeproduct  bigint

		, pricingmarginshape_id int
		, pricingmarginshapecell_id int
		, has_superset int
)
AS 
BEGIN



WITH 


  cte  AS (
  SELECT 

		-- === LHS Authority Level ===
		a.authoritylevel_id
		, a.authoritylevelorder
		, a.authoritylevelname
		
		-- === LHS Indicator Rate ===
		,a.indicatorrate


		-- === LHS Product Attributes ===
		--probably need to include all product structurals - and use as basis foppr the base rule 'co' values
		,a.product_id
		,a.loanpurpose_id
		--,a.loanamountband_id
		,a.[productterms_id]
		,a.[productgroup_id]
		,a.[packagetype_id]
		,a.[interestterms_id]
		,a.[interesttiming_id]
		,a.[repaymenttype_id]
		,a.[contracttype_id]
		,a.[interestterms_fixed_id]


		-- === LHS Discretion IDs ===
		,a.disc_product_id
		,a.disc_loanpurpose_id

		,a.[disc_productterms_id]
		,a.[disc_productgroup_id]
		,a.[disc_packagetype_id]
		,a.[disc_interestterms_id]
		,a.[disc_interesttiming_id]
		,a.[disc_repaymenttype_id]
		,a.[disc_contracttype_id]
		,a.[disc_interestterms_fixed_id]
		,a.disc_channel_id
		,a.disc_segmentgroup_id
		,a.disc_securitylocationgroup_id
		,a.disc_bankerbuidgroup_id
		,a.disc_competitorgroup_id
		,a.disc_cust_foreignresident_id
		,a.disc_cust_staff_id
		,a.disc_requesttype_id
		,a.disc_requesttypegroup_id
		,a.disc_introducercommission_id

		,a.disc_cust_lvrband_id
		,a.disc_cust_agglimitband_id
		,a.disc_cust_netutilband_id
		,a.disc_cust_riskweightband_id
		,a.disc_randomisedcontrolgroup_id

		,a.disc_cust_lvrband_system_id
		,a.disc_cust_agglimitband_system_id
		,a.disc_cust_netutilband_system_id
		,a.disc_cust_riskweightband_system_id
		,a.disc_randomisedcontrolgroup_system_id


		-- === LHS NA Flags ===
		,a.product_naflag
		,a.loanpurpose_naflag
		
		,a.[productterms_naflag]
		,a.[productgroup_naflag]
		,a.[packagetype_naflag]
		,a.[interestterms_naflag]
		,a.[interesttiming_naflag]
		,a.[repaymenttype_naflag]
		,a.[contracttype_naflag]
		,a.[interestterms_fixed_naflag]
		,a.channel_naflag

		,a.segmentgroup_naflag
		,a.securitylocationgroup_naflag
		,a.bankerbuidgroup_naflag
		,a.competitorgroup_naflag
		,a.cust_foreignresident_naflag
		,a.cust_staff_naflag
		,a.requesttype_naflag
		,a.requesttypegroup_naflag
		,a.introducercommission_naflag

		,a.cust_lvrband_naflag
		,a.cust_agglimitband_naflag
		,a.cust_netutilband_naflag
		,a.cust_riskweightband_naflag
		,a.randomisedcontrolgroup_naflag


		-- === LHS NA Flags Coalesced ===
		,a.product_naflag as co_product_naflag
		,a.loanpurpose_naflag as co_loanpurpose_naflag

		,a.productterms_naflag as co_productterms_naflag
		,a.productgroup_naflag as co_productgroup_naflag
		,a.packagetype_naflag as co_packagetype_naflag
		,a.interestterms_naflag as co_interestterms_naflag
		,a.interesttiming_naflag as co_interesttiming_naflag
		,a.repaymenttype_naflag as co_repaymenttype_naflag
		,a.contracttype_naflag as co_contracttype_naflag
		,a.interestterms_fixed_naflag as co_interestterms_fixed_naflag

		,a.channel_naflag as co_channel_naflag

		,a.segmentgroup_naflag as co_segmentgroup_naflag
		,a.securitylocationgroup_naflag as co_securitylocationgroup_naflag
		,a.bankerbuidgroup_naflag as co_bankerbuidgroup_naflag
		,a.competitorgroup_naflag as co_competitorgroup_naflag
		,a.cust_foreignresident_naflag as co_cust_foreignresident_naflag
		,a.cust_staff_naflag as co_cust_staff_naflag
		,a.requesttype_naflag as co_requesttype_naflag
		,a.requesttypegroup_naflag as co_requesttypegroup_naflag
		,a.introducercommission_naflag as co_introducercommission_naflag

		,a.cust_lvrband_naflag as co_cust_lvrband_naflag
		,a.cust_agglimitband_naflag as co_cust_agglimitband_naflag
		,a.cust_netutilband_naflag as co_cust_netutilband_naflag
		,a.cust_riskweightband_naflag as co_cust_riskweightband_naflag
		,a.randomisedcontrolgroup_naflag as co_randomisedcontrolgroup_naflag



		-- === LHS Coalesced Discretion Flags - Non Banded ===
		,product_id as co_disc_product_id
		,loanpurpose_id as co_disc_loanpurpose_id
		--,loanamountband_id as co_loanamountband_id
						
		,productgroup_id as co_disc_productgroup_id
		,packagetype_id as co_disc_packagetype_id
		,productterms_id as co_disc_productterms_id
		,interestterms_id as co_disc_interestterms_id
		,interesttiming_id as co_disc_interesttiming_id
		,repaymenttype_id as co_disc_repaymenttype_id
		,contracttype_id as co_disc_contracttype_id
		,interestterms_fixed_id as co_disc_interestterms_fixed_id
		
		,disc_channel_id as co_disc_channel_id
		,disc_segmentgroup_id as co_disc_segmentgroup_id
		,disc_securitylocationgroup_id as co_disc_securitylocationgroup_id
		,disc_competitorgroup_id as co_disc_competitorgroup_id
		,disc_bankerbuidgroup_id as co_disc_bankerbuidgroup_id
		
		,disc_cust_foreignresident_id as co_disc_cust_foreignresident_id
		,disc_cust_staff_id as co_disc_cust_staff_id
		,disc_requesttype_id as co_disc_requesttype_id
		,disc_requesttypegroup_id as co_disc_requesttypegroup_id
		,disc_introducercommission_id as co_disc_introducercommission_id


		-- === LHS HASHED AND Coalesced Discretion Flags - Non Banded ===
		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',

			cast(product_id as nvarchar(5))  +
			cast(loanpurpose_id as nvarchar(5))  +
			--,loanamountband_id as nvarchar(5))  +
						
			cast(productgroup_id as nvarchar(5))  +
			cast(packagetype_id as nvarchar(5))  +
			cast(productterms_id as nvarchar(5))  +
			cast(interestterms_id as nvarchar(5))  +
			cast(interesttiming_id as nvarchar(5))  +
			cast(repaymenttype_id as nvarchar(5))  +
			cast(contracttype_id as nvarchar(5))  +
			cast(interestterms_fixed_id as nvarchar(5))  +
		
			cast(disc_channel_id as nvarchar(5))  +
			cast(disc_segmentgroup_id as nvarchar(5))  +
			cast(disc_securitylocationgroup_id as nvarchar(5))  +
			cast(disc_competitorgroup_id as nvarchar(5))  +
			cast(disc_bankerbuidgroup_id as nvarchar(5))  +
		
			cast(disc_cust_foreignresident_id as nvarchar(5))  +
			cast(disc_cust_staff_id as nvarchar(5))  +
			cast(disc_requesttype_id as nvarchar(5))  +
			cast(disc_requesttypegroup_id as nvarchar(5))  +
			cast(disc_introducercommission_id as nvarchar(5))  
				), 2 ) as ruleset_nonbanded

		-- === LHS Coalesced Discretion Flags - Banded ===
		,disc_cust_lvrband_id as co_disc_cust_lvrband_id
		,disc_cust_agglimitband_id as co_disc_cust_agglimitband_id
		,disc_cust_netutilband_id as co_disc_cust_netutilband_id
		,disc_cust_riskweightband_id as co_disc_cust_riskweightband_id
		,disc_randomisedcontrolgroup_id as co_disc_randomisedcontrolgroup_id

		-- === LHS HASHED AND Coalesced Discretion Flags - Banded ===
		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',
			cast(disc_cust_lvrband_id as nvarchar(5))  +
			cast(disc_cust_agglimitband_id as nvarchar(5))  +
			cast(disc_cust_netutilband_id as nvarchar(5))  +
			cast(disc_cust_riskweightband_id as nvarchar(5))  +
			cast(disc_randomisedcontrolgroup_id as nvarchar(5)) 
		), 2 ) as ruleset_banded

		-- === LHS Coalesced Discretion Flags - Banding System ===
		,disc_cust_lvrband_system_id as co_disc_cust_lvrband_system_id
		,disc_cust_agglimitband_system_id as co_disc_cust_agglimitband_system_id
		,disc_cust_netutilband_system_id as co_disc_cust_netutilband_system_id
		,disc_cust_riskweightband_system_id as co_disc_cust_riskweightband_system_id
		,disc_randomisedcontrolgroup_system_id as co_disc_randomisedcontrolgroup_system_id

		-- === LHS HASHED AND Coalesced Discretion Flags - Banding System ===
		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',
			cast(disc_cust_lvrband_system_id as nvarchar(5))  +
			cast(disc_cust_agglimitband_system_id as nvarchar(5))  +
			cast(disc_cust_netutilband_system_id as nvarchar(5))  +
			cast(disc_cust_riskweightband_system_id as nvarchar(5))  +
			cast(disc_randomisedcontrolgroup_system_id as nvarchar(5)) 
		), 2 ) as ruleset_banding_system

		-- === LHS Coalesced Banding NA Flags  ===
 
		,CASE WHEN a.cust_lvrband_naflag = 1 then 0 			else 1 END +
		 CASE WHEN a.cust_agglimitband_naflag = 1 then 0 		else 1 END +
		 CASE WHEN a.cust_netutilband_naflag = 1 then 0 		else 1 END +
		 CASE WHEN a.cust_riskweightband_naflag = 1 then 0 		else 1 END +
		 CASE WHEN a.randomisedcontrolgroup_naflag = 1 then 0 	else 1 END as rule_num_disc_bandings

		-- === LHS Margins  ===
		,a.margin_value
		,cast(a.margin_value as float) as aggregate_margin

		,a.margin_value_desk
		,cast(a.margin_value_desk as float) as aggregate_margin_desk

		-- === LHS Recursion Control Fields  ===
		, 0 as level
		, CAST( a.pricingmarginshapecell_id AS VARCHAR(80) ) as combination
		, CAST( a.pricingmarginshape_id AS VARCHAR(80) ) as combination_shape

		, a.primevalue as combination_primeproduct

		, a.pricingmarginshape_id
		, a.pricingmarginshapecell_id
		
		--,a.product_disc_banding_dna
		
		
  FROM

  	pmx.sp_productpricingmatrix_discretion(@floor_type) a
	
	
  UNION ALL
  SELECT 


		-- === RHS Authority Level ===
  		b.authoritylevel_id
		, b.authoritylevelorder
		, b.authoritylevelname

		-- === RHS Indicator Rate ===
		,b.indicatorrate

		-- === RHS Product Attributes ===
		,b.product_id
		,b.loanpurpose_id
		--,b.loanamountband_id

		,b.[productterms_id] as [productterms_id]
		,b.[productgroup_id] as [productgroup_id]
		,b.[packagetype_id] as [packagetype_id]
		,b.[interestterms_id] as [interestterms_id]
		,b.[interesttiming_id] as [interesttiming_id]
		,b.[repaymenttype_id] as [repaymenttype_id]
		,b.[contracttype_id] as [contracttype_id]
		,b.[interestterms_fixed_id] as [interestterms_fixed_id]


		-- === RHS Discretion IDs ===
		,b.disc_product_id
		,b.disc_loanpurpose_id

		,b.[disc_productterms_id]
		,b.[disc_productgroup_id]
		,b.[disc_packagetype_id]
		,b.[disc_interestterms_id]
		,b.[disc_interesttiming_id]
		,b.[disc_repaymenttype_id]
		,b.[disc_contracttype_id]
		,b.[disc_interestterms_fixed_id]
		,b.disc_channel_id
		,b.disc_segmentgroup_id
		,b.disc_securitylocationgroup_id
		,b.disc_bankerbuidgroup_id
		,b.disc_competitorgroup_id
		,b.disc_cust_foreignresident_id
		,b.disc_cust_staff_id
		,b.disc_requesttype_id
		,b.disc_requesttypegroup_id
		,b.disc_introducercommission_id
		
		,b.disc_cust_lvrband_id
		,b.disc_cust_agglimitband_id
		,b.disc_cust_riskweightband_id
		,b.disc_cust_netutilband_id
		,b.disc_randomisedcontrolgroup_id


		,b.disc_cust_lvrband_system_id
		,b.disc_cust_agglimitband_system_id
		,b.disc_cust_riskweightband_system_id
		,b.disc_cust_netutilband_system_id
		,b.disc_randomisedcontrolgroup_system_id


		-- === RHS NA Flags ===
		,b.product_naflag
		,b.loanpurpose_naflag
		--,b.loanamountband_naflag

		,b.[productterms_naflag]
		,b.[productgroup_naflag]
		,b.[packagetype_naflag]
		,b.[interestterms_naflag]
		,b.[interesttiming_naflag]
		,b.[repaymenttype_naflag]
		,b.[contracttype_naflag]
		,b.[interestterms_fixed_naflag]
		,b.channel_naflag
		,b.segmentgroup_naflag
		,b.securitylocationgroup_naflag
		,b.bankerbuidgroup_naflag
		,b.competitorgroup_naflag
		,b.cust_foreignresident_naflag
		,b.cust_staff_naflag
		,b.requesttype_naflag
		,b.requesttypegroup_naflag
		,b.introducercommission_naflag

		,b.cust_lvrband_naflag
		,b.cust_agglimitband_naflag
		,b.cust_netutilband_naflag
		,b.cust_riskweightband_naflag
		,b.randomisedcontrolgroup_naflag


		-- === RHS NA Flags ===


		 ,isnull(coalesce(	CASE WHEN a.co_product_naflag = 1		then null else 0 END,  
							CASE WHEN b.product_naflag = 1			then null else 0 END), 1) as co_product_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_loanpurpose_naflag = 1	then null else 0 END,  
							CASE WHEN b.loanpurpose_naflag = 1		then null else 0 END), 1) as co_loanpurpose_naflag

		 ,isnull(coalesce(	CASE WHEN a.co_productterms_naflag = 1	then null else 0 END,  
							CASE WHEN b.productterms_naflag = 1		then null else 0 END), 1) as co_productterms_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_productgroup_naflag = 1	then null else 0 END,  
							CASE WHEN b.productgroup_naflag = 1		then null else 0 END), 1) as co_productgroup_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_packagetype_naflag = 1	then null else 0 END,  
							CASE WHEN b.packagetype_naflag = 1		then null else 0 END), 1) as co_packagetype_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_interestterms_naflag = 1 then null else 0 END,  
							CASE WHEN b.interestterms_naflag = 1	then null else 0 END), 1) as co_interestterms_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_interesttiming_naflag = 1 then null else 0 END,  
							CASE WHEN b.interesttiming_naflag = 1	then null else 0 END), 1) as co_interesttiming_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_repaymenttype_naflag = 1 then null else 0 END,  
							CASE WHEN b.repaymenttype_naflag = 1	then null else 0 END), 1) as co_repaymenttype_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_contracttype_naflag = 1	then null else 0 END,  
							CASE WHEN b.contracttype_naflag = 1		then null else 0 END), 1) as co_contracttype_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_interestterms_fixed_naflag = 1 	then null else 0 END,  
							CASE WHEN b.interestterms_fixed_naflag = 1 		then null else 0 END), 1) as co_interestterms_fixed_naflag

		 ,isnull(coalesce(	CASE WHEN a.co_channel_naflag = 1 				then null else 0 END,  
							CASE WHEN b.channel_naflag = 1					then null else 0 END), 1) as co_channel_naflag

		 ,isnull(coalesce(	CASE WHEN a.co_segmentgroup_naflag = 1 			then null else 0 END,  
							CASE WHEN b.segmentgroup_naflag = 1				then null else 0 END), 1) as co_segmentgroup_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_securitylocationgroup_naflag = 1 then null else 0 END,  
							CASE WHEN b.securitylocationgroup_naflag = 1	then null else 0 END), 1) as co_securitylocationgroup_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_competitorgroup_naflag = 1 		then null else 0 END,  
							CASE WHEN b.competitorgroup_naflag = 1			then null else 0 END), 1) as co_competitorgroup_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_bankerbuidgroup_naflag = 1 		then null else 0 END,  
							CASE WHEN b.bankerbuidgroup_naflag = 1			then null else 0 END), 1) as co_bankerbuidgroup_naflag

		 ,isnull(coalesce(	CASE WHEN a.co_cust_foreignresident_naflag = 1 	then null else 0 END,  
							CASE WHEN b.cust_foreignresident_naflag = 1		then null else 0 END), 1) as co_cust_foreignresident_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_cust_staff_naflag = 1 			then null else 0 END,  
							CASE WHEN b.cust_staff_naflag = 1				then null else 0 END), 1) as co_cust_staff_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_requesttype_naflag = 1 			then null else 0 END,  
							CASE WHEN b.requesttype_naflag = 1				then null else 0 END), 1) as co_requesttype_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_requesttypegroup_naflag = 1 			then null else 0 END,  
							CASE WHEN b.requesttypegroup_naflag = 1				then null else 0 END), 1) as co_requesttypegroup_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_introducercommission_naflag = 1 	then null else 0 END,  
							CASE WHEN b.introducercommission_naflag = 1		then null else 0 END), 1) as co_introducercommission_naflag

		 ,isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 	then null else 0 END,  
							CASE WHEN b.cust_lvrband_naflag = 1		then null else 0 END), 1) as co_cust_lvrband_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 	then null else 0 END,  
							CASE WHEN b.cust_agglimitband_naflag = 1		then null else 0 END), 1) as co_cust_agglimitband_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 	then null else 0 END,  
							CASE WHEN b.cust_netutilband_naflag = 1		then null else 0 END), 1) as co_cust_netutilband_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 	then null else 0 END,  
							CASE WHEN b.cust_riskweightband_naflag = 1		then null else 0 END), 1) as co_cust_riskweightband_naflag
		 ,isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 	then null else 0 END,  
							CASE WHEN b.randomisedcontrolgroup_naflag = 1		then null else 0 END), 1) as co_randomisedcontrolgroup_naflag




		-- === RHS Coalesced Discretion Flags - Non Banded ===
		-- use a.co_ versions... to get full history.

			,isnull(coalesce(	CASE WHEN a.co_product_naflag = 1 then null 				else a.co_disc_product_id END,  
								CASE WHEN b.product_naflag = 1 then null 					else b.disc_product_id END), a.co_disc_product_id) as co_disc_product_id
			,isnull(coalesce(	CASE WHEN a.co_loanpurpose_naflag = 1 then null 			else a.co_disc_loanpurpose_id END,  
								CASE WHEN b.loanpurpose_naflag = 1 then null 				else b.disc_loanpurpose_id END), a.co_disc_loanpurpose_id) as co_disc_loanpurpose_id
						
			,isnull(coalesce(	CASE WHEN a.co_productgroup_naflag = 1 then null 			else a.co_disc_productgroup_id END,  
								CASE WHEN b.productgroup_naflag = 1 then null 				else b.disc_productgroup_id END), a.co_disc_productgroup_id) as co_disc_productgroup_id
			,isnull(coalesce(	CASE WHEN a.co_packagetype_naflag = 1 then null 			else a.co_disc_packagetype_id END,  
								CASE WHEN b.packagetype_naflag = 1 then null 				else b.disc_packagetype_id END), a.co_disc_packagetype_id) as co_disc_packagetype_id
			,isnull(coalesce(	CASE WHEN a.co_productterms_naflag = 1 then null 			else a.co_disc_productterms_id END,  
								CASE WHEN b.productterms_naflag = 1 then null 				else b.disc_productterms_id END), a.co_disc_productterms_id) as co_disc_productterms_id
			,isnull(coalesce(	CASE WHEN a.co_interestterms_naflag = 1 then null 			else a.co_disc_interestterms_id END,  
								CASE WHEN b.interestterms_naflag = 1 then null 				else b.disc_interestterms_id END), a.co_disc_interestterms_id) as co_disc_interestterms_id
			,isnull(coalesce(	CASE WHEN a.co_interesttiming_naflag = 1 then null 			else a.co_disc_interesttiming_id END,  
								CASE WHEN b.interesttiming_naflag = 1 then null 			else b.disc_interesttiming_id END), a.co_disc_interesttiming_id) as co_disc_interesttiming_id
			,isnull(coalesce(	CASE WHEN a.co_repaymenttype_naflag = 1 then null 			else a.co_disc_repaymenttype_id END,  
								CASE WHEN b.repaymenttype_naflag = 1 then null 				else b.disc_repaymenttype_id END), a.co_disc_repaymenttype_id) as co_disc_repaymenttype_id
			,isnull(coalesce(	CASE WHEN a.co_contracttype_naflag = 1 then null 			else a.co_disc_contracttype_id END,  
								CASE WHEN b.contracttype_naflag = 1 then null 				else b.disc_contracttype_id END), a.co_disc_contracttype_id) as co_disc_contracttype_id
			,isnull(coalesce(	CASE WHEN a.co_interestterms_fixed_naflag = 1 then null 	else a.co_disc_interestterms_fixed_id END,  
								CASE WHEN b.interestterms_fixed_naflag = 1 then null 		else b.disc_interestterms_fixed_id END), a.co_disc_interestterms_fixed_id) as co_disc_interestterms_fixed_id
				
			,isnull(coalesce(	CASE WHEN a.co_channel_naflag = 1 then null 				else a.co_disc_channel_id END,  
								CASE WHEN b.channel_naflag = 1 then null 					else b.disc_channel_id END), a.co_disc_channel_id) as co_disc_channel_id
		
			,isnull(coalesce(	CASE WHEN a.co_segmentgroup_naflag = 1 then null 			else a.co_disc_segmentgroup_id END,  
								CASE WHEN b.segmentgroup_naflag = 1 then null 				else b.disc_segmentgroup_id END), a.co_disc_segmentgroup_id) as co_disc_segmentgroup_id
			,isnull(coalesce(	CASE WHEN a.co_securitylocationgroup_naflag = 1 then null 	else a.co_disc_securitylocationgroup_id END,  
								CASE WHEN b.securitylocationgroup_naflag = 1 then null 		else b.disc_securitylocationgroup_id END), a.co_disc_securitylocationgroup_id) as co_disc_securitylocationgroup_id
			,isnull(coalesce(	CASE WHEN a.co_competitorgroup_naflag = 1 then null 		else a.co_disc_competitorgroup_id END,  
								CASE WHEN b.competitorgroup_naflag = 1 then null 			else b.disc_competitorgroup_id END), a.co_disc_competitorgroup_id) as co_disc_competitorgroup_id
			,isnull(coalesce(	CASE WHEN a.co_bankerbuidgroup_naflag = 1 then null 		else a.co_disc_bankerbuidgroup_id END,  
								CASE WHEN b.bankerbuidgroup_naflag = 1 then null 			else b.disc_bankerbuidgroup_id END), a.co_disc_bankerbuidgroup_id) as co_disc_bankerbuidgroup_id
		
			,isnull(coalesce(	CASE WHEN a.co_cust_foreignresident_naflag = 1 then null 	else a.co_disc_cust_foreignresident_id END,  
								CASE WHEN b.cust_foreignresident_naflag = 1 then null 		else b.disc_cust_foreignresident_id END), a.co_disc_cust_foreignresident_id) as co_disc_cust_foreignresident_id
			,isnull(coalesce(	CASE WHEN a.co_cust_staff_naflag = 1 then null 				else a.co_disc_cust_staff_id END,  
								CASE WHEN b.cust_staff_naflag = 1 then null 				else b.disc_cust_staff_id END), a.co_disc_cust_staff_id) as co_disc_cust_staff_id
			,isnull(coalesce(	CASE WHEN a.co_requesttype_naflag = 1 then null 			else a.co_disc_requesttype_id END,  
								CASE WHEN b.requesttype_naflag = 1 then null 				else b.disc_requesttype_id END), a.co_disc_requesttype_id) as co_disc_requesttype_id
			,isnull(coalesce(	CASE WHEN a.co_requesttypegroup_naflag = 1 then null 			else a.co_disc_requesttypegroup_id END,  
								CASE WHEN b.requesttypegroup_naflag = 1 then null 				else b.disc_requesttypegroup_id END), a.co_disc_requesttypegroup_id) as co_disc_requesttypegroup_id
			,isnull(coalesce(	CASE WHEN a.co_introducercommission_naflag = 1 then null 	else a.co_disc_introducercommission_id END,  
								CASE WHEN b.introducercommission_naflag = 1 then null 		else b.disc_introducercommission_id END), a.co_disc_introducercommission_id) as co_disc_introducercommission_id


		-- === RHS HASHED AND Coalesced Discretion Flags - Non Banded ===
		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',

			cast(isnull(coalesce(	CASE WHEN a.co_product_naflag = 1 then null 			else a.co_disc_product_id END,  
									CASE WHEN b.product_naflag = 1 then null 				else b.disc_product_id END), a.co_disc_product_id) as nvarchar(5)) +
			cast(isnull(coalesce(	CASE WHEN a.co_loanpurpose_naflag = 1 then null 		else a.co_disc_loanpurpose_id END,  
									CASE WHEN b.loanpurpose_naflag = 1 then null 			else b.disc_loanpurpose_id END), a.co_disc_loanpurpose_id)as nvarchar(5)) +
						
			cast(isnull(coalesce(	CASE WHEN a.co_productgroup_naflag = 1 then null 		else a.co_disc_productgroup_id END,  
									CASE WHEN b.productgroup_naflag = 1 then null 			else b.disc_productgroup_id END), a.co_disc_productgroup_id) as nvarchar(5)) +
			cast(isnull(coalesce(	CASE WHEN a.co_packagetype_naflag = 1 then null 		else a.co_disc_packagetype_id END,  
									CASE WHEN b.packagetype_naflag = 1 then null 			else b.disc_packagetype_id END), a.co_disc_packagetype_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_productterms_naflag = 1 then null 		else a.co_disc_productterms_id END,  
									CASE WHEN b.productterms_naflag = 1 then null 			else b.disc_productterms_id END), a.co_disc_productterms_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_interestterms_naflag = 1 then null 		else a.co_disc_interestterms_id END,  
									CASE WHEN b.interestterms_naflag = 1 then null 			else b.disc_interestterms_id END), a.co_disc_interestterms_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_interesttiming_naflag = 1 then null 		else a.co_disc_interesttiming_id END,  
									CASE WHEN b.interesttiming_naflag = 1 then null 		else b.disc_interesttiming_id END), a.co_disc_interesttiming_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_repaymenttype_naflag = 1 then null 		else a.co_disc_repaymenttype_id END,  
									CASE WHEN b.repaymenttype_naflag = 1 then null 			else b.disc_repaymenttype_id END), a.co_disc_repaymenttype_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_contracttype_naflag = 1 then null 		else a.co_disc_contracttype_id END,  
									CASE WHEN b.contracttype_naflag = 1 then null 			else b.disc_contracttype_id END), a.co_disc_contracttype_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_interestterms_fixed_naflag = 1 then null else a.co_disc_interestterms_fixed_id END,  
									CASE WHEN b.interestterms_fixed_naflag = 1 then null 	else b.disc_interestterms_fixed_id END), a.co_disc_interestterms_fixed_id) as nvarchar(5))  +
				
			cast(isnull(coalesce(	CASE WHEN a.co_channel_naflag = 1 then null 			else a.co_disc_channel_id END,  
									CASE WHEN b.channel_naflag = 1 then null 				else b.disc_channel_id END), a.co_disc_channel_id) as nvarchar(5))  +
		
			cast(isnull(coalesce(	CASE WHEN a.co_segmentgroup_naflag = 1 then null 		else a.co_disc_segmentgroup_id END,  
									CASE WHEN b.segmentgroup_naflag = 1 then null 			else b.disc_segmentgroup_id END), a.co_disc_segmentgroup_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_securitylocationgroup_naflag = 1 then null else a.co_disc_securitylocationgroup_id END,  
									CASE WHEN b.securitylocationgroup_naflag = 1 then null 	else b.disc_securitylocationgroup_id END), a.co_disc_securitylocationgroup_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_competitorgroup_naflag = 1 then null 	else a.co_disc_competitorgroup_id END,  
									CASE WHEN b.competitorgroup_naflag = 1 then null 		else b.disc_competitorgroup_id END), a.co_disc_competitorgroup_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_bankerbuidgroup_naflag = 1 then null 	else a.co_disc_bankerbuidgroup_id END,  
									CASE WHEN b.bankerbuidgroup_naflag = 1 then null 		else b.disc_bankerbuidgroup_id END), a.co_disc_bankerbuidgroup_id) as nvarchar(5))  +
		
			cast(isnull(coalesce(	CASE WHEN a.co_cust_foreignresident_naflag = 1 then null else a.co_disc_cust_foreignresident_id END,  
									CASE WHEN b.cust_foreignresident_naflag = 1 then null 	else b.disc_cust_foreignresident_id END), a.co_disc_cust_foreignresident_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_staff_naflag = 1 then null 			else a.co_disc_cust_staff_id END,  
									CASE WHEN b.cust_staff_naflag = 1 then null 			else b.disc_cust_staff_id END), a.co_disc_cust_staff_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_requesttype_naflag = 1 then null 		else a.co_disc_requesttype_id END,  
									CASE WHEN b.requesttype_naflag = 1 then null 			else b.disc_requesttype_id END), a.co_disc_requesttype_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_requesttypegroup_naflag = 1 then null 		else a.co_disc_requesttypegroup_id END,  
									CASE WHEN b.requesttypegroup_naflag = 1 then null 			else b.disc_requesttypegroup_id END), a.co_disc_requesttypegroup_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_introducercommission_naflag = 1 then null else a.co_disc_introducercommission_id END,  
									CASE WHEN b.introducercommission_naflag = 1 then null 	else b.disc_introducercommission_id END), a.co_disc_introducercommission_id) as nvarchar(5))  
				), 2 ) as ruleset_nonbanded
		

		-- === RHS Coalesced Discretion Flags - Banded ===

			,isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 then null 			else a.co_disc_cust_lvrband_id END, 
								CASE WHEN b.cust_lvrband_naflag = 1 then null 				else b.disc_cust_lvrband_id END), a.co_disc_cust_lvrband_id) as co_disc_cust_lvrband_id
			,isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 then null 		else a.co_disc_cust_agglimitband_id END,  
								CASE WHEN b.cust_agglimitband_naflag = 1 then null			else b.disc_cust_agglimitband_id END), a.co_disc_cust_agglimitband_id) as co_disc_cust_agglimitband_id
			,isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 then null 		else a.co_disc_cust_netutilband_id END,  
								CASE WHEN b.cust_netutilband_naflag = 1 then null 			else b.disc_cust_netutilband_id END), a.co_disc_cust_netutilband_id) as co_disc_cust_netutilband_id
			,isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 then null 	else a.co_disc_cust_riskweightband_id END,  
								CASE WHEN b.cust_riskweightband_naflag = 1 then null 		else b.disc_cust_riskweightband_id END), a.co_disc_cust_riskweightband_id) as co_disc_cust_riskweightband_id
			,isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 then null 	else a.co_disc_randomisedcontrolgroup_id END,  
								CASE WHEN b.randomisedcontrolgroup_naflag = 1 then null 	else b.disc_randomisedcontrolgroup_id END), a.co_disc_randomisedcontrolgroup_id) as co_disc_randomisedcontrolgroup_id
		
		-- === RHS HASHED AND Coalesced Discretion Flags - Banded ===

		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',
			cast(isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 then null 			else a.co_disc_cust_lvrband_id END, 
									CASE WHEN b.cust_lvrband_naflag = 1 then null 				else b.disc_cust_lvrband_id END), a.co_disc_cust_lvrband_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 then null 		else a.co_disc_cust_agglimitband_id END,  
									CASE WHEN b.cust_agglimitband_naflag = 1 then null			else b.disc_cust_agglimitband_id END), a.co_disc_cust_agglimitband_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 then null 		else a.co_disc_cust_netutilband_id END,  
									CASE WHEN b.cust_netutilband_naflag = 1 then null 			else b.disc_cust_netutilband_id END), a.co_disc_cust_netutilband_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 then null 	else a.co_disc_cust_riskweightband_id END,  
									CASE WHEN b.cust_riskweightband_naflag = 1 then null 		else b.disc_cust_riskweightband_id END), a.co_disc_cust_riskweightband_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 then null 	else a.co_disc_randomisedcontrolgroup_id END,  
									CASE WHEN b.randomisedcontrolgroup_naflag = 1 then null 	else b.disc_randomisedcontrolgroup_id END), a.co_disc_randomisedcontrolgroup_id) as nvarchar(5))  
		), 2 ) as ruleset_banded


		-- === RHS Coalesced Discretion Flags - Banding Systems ===

			,isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 then null 			else a.co_disc_cust_lvrband_system_id END, 
								CASE WHEN b.cust_lvrband_naflag = 1 then null 				else b.disc_cust_lvrband_system_id END), a.co_disc_cust_lvrband_system_id) as co_disc_cust_lvrband_system_id
			,isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 then null 		else a.co_disc_cust_agglimitband_system_id END,  
								CASE WHEN b.cust_agglimitband_naflag = 1 then null			else b.disc_cust_agglimitband_system_id END), a.co_disc_cust_agglimitband_system_id) as co_disc_cust_agglimitband_system_id
			,isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 then null 		else a.co_disc_cust_netutilband_system_id END,  
								CASE WHEN b.cust_netutilband_naflag = 1 then null 			else b.disc_cust_netutilband_system_id END), a.co_disc_cust_netutilband_system_id) as co_disc_cust_netutilband_system_id
			,isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 then null 	else a.co_disc_cust_riskweightband_system_id END,  
								CASE WHEN b.cust_riskweightband_naflag = 1 then null 		else b.disc_cust_riskweightband_system_id END), a.co_disc_cust_riskweightband_system_id) as co_disc_cust_riskweightband_system_id
			,isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 then null 	else a.co_disc_randomisedcontrolgroup_system_id END,  
								CASE WHEN b.randomisedcontrolgroup_naflag = 1 then null 	else b.disc_randomisedcontrolgroup_system_id END), a.co_disc_randomisedcontrolgroup_system_id) as co_disc_randomisedcontrolgroup_system_id
		
		-- === RHS HASHED AND Coalesced Discretion Flags - Banding System ===

		,CONVERT(nvarchar(40),	HASHBYTES('SHA1',
			cast(isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 then null 		else a.co_disc_cust_lvrband_system_id END, 
									CASE WHEN b.cust_lvrband_naflag = 1 then null 			else b.disc_cust_lvrband_system_id END), a.co_disc_cust_lvrband_system_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 then null 	else a.co_disc_cust_agglimitband_system_id END,  
									CASE WHEN b.cust_agglimitband_naflag = 1 then null		else b.disc_cust_agglimitband_system_id END), a.co_disc_cust_agglimitband_system_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 then null 	else a.co_disc_cust_netutilband_system_id END,  
									CASE WHEN b.cust_netutilband_naflag = 1 then null 		else b.disc_cust_netutilband_system_id END), a.co_disc_cust_netutilband_system_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 then null else a.co_disc_cust_riskweightband_system_id END,  
									CASE WHEN b.cust_riskweightband_naflag = 1 then null 		else b.disc_cust_riskweightband_system_id END), a.co_disc_cust_riskweightband_system_id) as nvarchar(5))  +
			cast(isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 then null else a.co_disc_randomisedcontrolgroup_system_id END,  
									CASE WHEN b.randomisedcontrolgroup_naflag = 1 then null		else b.disc_randomisedcontrolgroup_system_id END), a.co_disc_randomisedcontrolgroup_system_id) as nvarchar(5))  
		), 2 ) as ruleset_bandingsystem


		-- === RHS Coalesced Banding NA Flags  ===


		
			,isnull(coalesce(	CASE WHEN a.co_cust_lvrband_naflag = 1 then null 			else 1 END,  
								CASE WHEN b.cust_lvrband_naflag = 1 then null 				else 1 END), 0) +
			isnull(coalesce(	CASE WHEN a.co_cust_agglimitband_naflag = 1 then null 		else 1 END,  
								CASE WHEN b.cust_agglimitband_naflag = 1 then null 			else 1 END), 0) +
			isnull(coalesce(	CASE WHEN a.co_cust_netutilband_naflag = 1 then null 		else 1 END,  
								CASE WHEN b.cust_netutilband_naflag = 1 then null 			else 1 END), 0) +
			isnull(coalesce(	CASE WHEN a.co_cust_riskweightband_naflag = 1 then null 	else 1 END,  
								CASE WHEN b.cust_riskweightband_naflag = 1 then null 		else 1 END), 0) +
			isnull(coalesce(	CASE WHEN a.co_randomisedcontrolgroup_naflag = 1 then null 	else 1 END,  
								CASE WHEN b.randomisedcontrolgroup_naflag = 1 then null 	else 1 END), 0) as rule_num_disc_bandings



		-- === RHS Margins  ===

		, b.margin_value
		, a.aggregate_margin + cast(b.margin_value as float) as aggregate_margin

		, b.margin_value_desk
		, a.aggregate_margin_desk + cast(b.margin_value_desk as float) as aggregate_margin_desk

		-- === RHS Recursion Control Fields  ===
		,a.level+1 as level
		,CAST( a.combination + ',' + CAST( b.pricingmarginshapecell_id AS NVARCHAR(5) ) AS VARCHAR(80) ) as combination
		,CAST( a.combination_shape + ',' + CAST( b.pricingmarginshape_id AS NVARCHAR(5) ) AS VARCHAR(80) ) as combination_shape


		--=== The prime DNA of the ruleset ===--		
		,a.combination_primeproduct * b.primevalue as combination_primeproduct 

		, b.pricingmarginshape_id
		,b.pricingmarginshapecell_id
		
		--,b.product_disc_banding_dna
		
		

  FROM

    	pmx.sp_productpricingmatrix_discretion(@floor_type) b


-- ==== RECURSIVELY JOIN ON RULES THAT MATCH 
---   This references back to the CTE, using the same alias (a) as the root...

  INNER JOIN cte a
         
         
         -- =====  RULE MATCHING CRITERIA =======
         -- This uses three valued logic to determine whether rules agree
         -- The first condition is a hard match
         -- The second condition is a soft match, where either the LHS or the RHS have a "Don't Care" flag
         
		 ON
			a.authoritylevel_id = b.authoritylevel_id 
			AND a.product_id = b.product_id
			AND a.loanpurpose_id = b.loanpurpose_id

					
			--product Linkage
			AND 
				(	(a.[co_disc_product_id] = b.[disc_product_id] ) OR
						a.[co_product_naflag]  = 1 OR b.[product_naflag]  = 1
				)
			AND 
				(	(a.[co_disc_loanpurpose_id] = b.[disc_loanpurpose_id] ) OR
						a.[co_loanpurpose_naflag]  = 1 OR b.[loanpurpose_naflag]  = 1
				)										
			AND 
				(	(a.[co_disc_productterms_id] = b.[disc_productterms_id] ) OR
						a.[co_productterms_naflag]  = 1 OR b.[productterms_naflag]  = 1
				)	
			AND 
				(	(a.[co_disc_productgroup_id] = b.[disc_productgroup_id] ) OR
						a.[co_productgroup_naflag]  = 1 OR b.[productgroup_naflag]  = 1
				)
			AND 
				(	(a.[co_disc_packagetype_id] = b.[disc_packagetype_id] ) OR
						a.[co_packagetype_naflag]  = 1 OR b.[packagetype_naflag]  = 1
				)			
					 
			--Product Attributes
			AND 
				(	(a.[co_disc_interestterms_id] = b.[disc_interestterms_id] ) OR
						a.[co_interestterms_naflag]  = 1 OR a.[interestterms_naflag]  = 1
				)
			AND 
				(	(a.[co_disc_interesttiming_id] = b.[disc_interesttiming_id] ) OR
						a.[co_interesttiming_naflag]  = 1 OR b.[interesttiming_naflag]  = 1
				)
			AND 
				(	(a.[co_disc_repaymenttype_id] = b.[disc_repaymenttype_id] ) OR
						a.[co_repaymenttype_naflag]  = 1 OR b.[repaymenttype_naflag]  = 1
				)
			AND 
				(	(a.[co_disc_contracttype_id] = b.[disc_contracttype_id] ) OR
						a.[co_contracttype_naflag]  = 1 OR b.[contracttype_naflag]  = 1
				)								
			AND 
				(	(a.[co_disc_interestterms_fixed_id] = b.[disc_interestterms_fixed_id] ) OR
						a.[co_interestterms_fixed_naflag] = 1 OR b.[interestterms_fixed_naflag] = 1
				)	
		
			--channels
			AND 
				(	(a.co_disc_channel_id = b.disc_channel_id ) OR
						a.co_channel_naflag = 1 OR b.channel_naflag = 1
				)	
					
					
					
			--bandings
			AND 
				(	(a.co_disc_cust_lvrband_id = b.disc_cust_lvrband_id ) OR
						a.co_cust_lvrband_naflag = 1 OR b.cust_lvrband_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_cust_agglimitband_id = b.disc_cust_agglimitband_id ) OR
						a.co_cust_agglimitband_naflag = 1 OR b.cust_agglimitband_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_cust_netutilband_id = b.disc_cust_netutilband_id ) OR
						a.co_cust_netutilband_naflag = 1 OR b.cust_netutilband_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_cust_riskweightband_id = b.disc_cust_riskweightband_id ) OR
						a.co_cust_riskweightband_naflag = 1 OR b.cust_riskweightband_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_randomisedcontrolgroup_id = b.disc_randomisedcontrolgroup_id ) OR
						a.co_randomisedcontrolgroup_naflag = 1 OR b.randomisedcontrolgroup_naflag = 1
				)	
					
			--Grouped dims
			AND 
				(	(a.co_disc_segmentgroup_id = b.disc_segmentgroup_id ) OR
						a.co_segmentgroup_naflag = 1 OR b.segmentgroup_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_securitylocationgroup_id = b.disc_securitylocationgroup_id ) OR
						a.co_securitylocationgroup_naflag = 1 OR b.securitylocationgroup_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_competitorgroup_id = b.disc_competitorgroup_id ) OR
						a.co_competitorgroup_naflag = 1 OR b.competitorgroup_naflag = 1 
				)	
		
			AND 
				(	(a.co_disc_bankerbuidgroup_id = b.disc_bankerbuidgroup_id ) OR
						a.co_bankerbuidgroup_naflag = 1 OR b.bankerbuidgroup_naflag = 1
				)	
					
			-- Booleans and other dims
			AND 
				(	(a.co_disc_cust_foreignresident_id = b.disc_cust_foreignresident_id ) OR
						a.co_cust_foreignresident_naflag = 1 OR b.cust_foreignresident_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_cust_staff_id = b.disc_cust_staff_id ) OR
						a.co_cust_staff_naflag = 1 OR b.cust_staff_naflag = 1
				)	
		
			AND 
				(	(a.co_disc_requesttype_id = b.disc_requesttype_id ) OR
						a.co_requesttype_naflag = 1 OR b.requesttype_naflag  = 1 
				)	
			AND 
				(	(a.co_disc_requesttypegroup_id = b.disc_requesttypegroup_id ) OR
						a.co_requesttypegroup_naflag = 1 OR b.requesttypegroup_naflag  = 1 
				)				
				
			AND 
				(	(a.co_disc_introducercommission_id = b.disc_introducercommission_id ) OR
						a.co_introducercommission_naflag = 1 OR b.introducercommission_naflag = 1 
				)

		-- Only a one-way combination
        AND  ( a.pricingmarginshapecell_id < b.pricingmarginshapecell_id )
		AND  (a.pricingmarginshape_id <> b.pricingmarginshape_id)


)

--==== FINAL FILTERING BASED ON PRIME SUPERSET LOGIC...
INSERT @t
select 

	--==== Base set of attributes from cte1
	cte_rules.*
	
	--==== Filtering Criteria
	, cte_prime.has_superset

from cte cte_rules

	inner join 
	-- Find SUPERSET Rules from early iterations
	(	
		select
		aa.product_id
		, aa.loanpurpose_id
		--, aa.authoritylevel_id
		, aa.ruleset_nonbanded
		, aa.combination
		, aa.combination_primeproduct
		, max(isnull(bb.is_superset, 0)) as has_superset
		 
		 from cte aa
		 left join
	
			(select 1 as is_superset
				, product_id
				, loanpurpose_id
				--, authoritylevel_id
				,combination as superset_combination
				,combination_primeproduct as superset_combination_primeproduct
				,ruleset_nonbanded as superset_ruleset_nonbanded
				from cte
			) bb
			--on bb.superset_combination  LIKE  (aa.combination+'%')
			on
			
			--same product and loan purpose
			aa.product_id = bb.product_id
			and aa.loanpurpose_id = bb.loanpurpose_id
			--and aa.authoritylevel_id = bb.authoritylevel_id
			
			-- and in the same ruleset - based on rule attributes. This effectively filters out overhangs from banded rules.
			and aa.ruleset_nonbanded = bb.superset_ruleset_nonbanded
	
			-- not the same rule
			and aa.combination <> bb.superset_combination
	
			--=== PRIME FILTER CALCULATION ===---
			-- If the remainder is not 1, then we have divided by a non-factor of the prime combination
			-- therefore the rule is not a subset.
			and (bb.superset_combination_primeproduct/cast(aa.combination_primeproduct as numeric)) % 1 = 0
	
		group by 
		  aa.product_id
		, aa.loanpurpose_id
		--, aa.authoritylevel_id
		, aa.ruleset_nonbanded
		, aa.combination
		, aa.combination_primeproduct
	
	) cte_prime

	on cte_rules.combination = cte_prime.combination
	and cte_rules.product_id = cte_prime.product_id
	and cte_rules.loanpurpose_id = cte_prime.loanpurpose_id
	--and cte_rules.authoritylevel_id = cte_prime.authoritylevel_id

	-- The PRIME FILTER - keep only supersets!
	and cte_prime.has_superset = 0




RETURN 
END
;


