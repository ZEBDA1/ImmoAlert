from __future__ import annotations
from datetime import datetime,timezone
import json
import unittest
from services.listing_features import normalize_features,format_date

class ListingFeaturePresentationTests(unittest.TestCase):
 def test_internal_portal_metadata_is_hidden_instead_of_given_invented_labels(self):
  raw={key:value for key,value in [('activity_sector',2),('external_ad_id','netty_22741_2111025'),('district_id',3000759),('district_visibility',True),('district_type_id',9),('district_resolution_type','integration'),('has_visibility_option',True),('visibility_option_products','RE_VISIBILITY_FEATURE'),('store_logo','https://img.test/logo.jpg'),('online_store_id',38238),('is_import',True),('rating_score',4.5),('rating_count',13),('profile_picture_url','https://img.test/profile.jpg'),('floor_display',True)]}
  self.assertEqual(normalize_features(raw),{})

 def test_business_fields_have_french_labels_and_values(self):
  raw={'store_name':'AGENCE DE LA GARE','mandate_type':'exclusive','lease_type':'rent','Ce bien est :':'Non meublé','Étage de votre bien':'2','Nombre de chambres':'2 ch.','Honoraires':'605','Charges incluses':'true','Places de parking':'0','Mode de chauffage':'electric'}
  result=normalize_features(raw)
  self.assertEqual(result,{'Annonceur':'AGENCE DE LA GARE','Mandat':'Exclusif','Type de transaction':'Location','Location meublée':'Non','Étage':'2e étage','Chambres':'2','Honoraires de location':'605 €','Charges comprises':'Oui','Places de parking':'Aucune','Énergie du chauffage':'Électrique'})

 def test_source_timestamps_use_paris_time_and_padded_seconds(self):
  self.assertEqual(format_date('2026-10-26T15:04:02.000Z'),'26/10/2026 16:04:02')
  self.assertEqual(format_date('2026-10-09T23:39:00.000Z'),'10/10/2026 01:39:00')
  self.assertEqual(format_date('2026-10-26T16:04:02+01:00'),'26/10/2026 16:04:02')
  self.assertEqual(format_date('2026-10-26 16:04:02'),'26/10/2026 16:04:02')

 def test_date_only_is_not_given_a_fabricated_time(self):
  self.assertEqual(normalize_features({'Date de publication':'2026-10-26','Disponible à partir de':'2026-11-01'}),{'Date de publication':'26/10/2026','Disponibilité':'01/11/2026'})
  self.assertEqual(format_date('26/10/2026'),'26/10/2026')

 def test_dst_change_and_midnight_do_not_use_a_fixed_utc_offset(self):
  self.assertEqual(format_date('2026-10-25T00:30:02Z'),'25/10/2026 02:30:02')
  self.assertEqual(format_date('2026-10-25T01:30:02Z'),'25/10/2026 02:30:02')
  self.assertEqual(format_date('2026-10-26T23:00:00Z'),'27/10/2026 00:00:00')

 def test_units_and_currency_are_formatted_only_for_known_quantities(self):
  result=normalize_features({'Surface habitable\n\n':'55.5','Charges locatives':'35.50','Dépôt de garantie':'1 600 €','Montant annuel énergétique minimum':'490','Année de construction':'1948','Référence':'LA11025-BYG2S'})
  self.assertEqual(result['Surface habitable'],'55,5 m²');self.assertEqual(result['Charges locatives'],'35,50 €');self.assertEqual(result['Dépôt de garantie'],'1 600 €')
  self.assertEqual(result['Dépenses énergétiques annuelles minimales'],'490 € / an');self.assertEqual(result['Année de construction'],'1948');self.assertEqual(result['Référence de l’annonce'],'LA11025-BYG2S')

 def test_boolean_false_and_zero_counts_are_not_dropped(self):
  result=normalize_features({'Ascenseur':False,'Nombre de salles de bain':0,'Charges incluses':'false','Étage':0})
  self.assertEqual(result,{'Ascenseur':'Non','Salles de bain':'0','Charges comprises':'Non','Étage':'Rez-de-chaussée'})

 def test_undocumented_numeric_taxonomies_are_not_guessed(self):
  self.assertEqual(normalize_features({'real_estate_type':'2','heating_mode':'4'}),{})

 def test_existing_readable_labels_are_kept_and_duplicate_aliases_collapse(self):
  result=normalize_features({'rooms':'2','Nombre de pièces':'3','numberOfRooms':'4','Prestation particulière':'Pierres apparentes','DPE':'c','Classe énergie':'C'})
  self.assertEqual(result['Pièces'],'3');self.assertEqual(result['Classe énergie (DPE)'],'C');self.assertEqual(result['Prestation particulière'],'Pierres apparentes')

 def test_malformed_values_do_not_break_a_stored_listing(self):
  for raw in [None,'not json','[]',[],123]:self.assertEqual(normalize_features(raw),{})
  self.assertEqual(normalize_features({'Date de publication':'2026-99-99T23:39:00Z','Surface':float('nan'),'Chauffage':None}),{})

 def test_raw_data_is_preserved_and_public_links_are_the_only_allowed_urls(self):
  raw={'DPE':'C','Date de publication':'2026-10-26T15:04:02Z','virtual_tour':'https://tour.example.test/one','pro_rates_link':'javascript:alert(1)','Logo secondaire':'https://img.test/logo.jpg'}
  original=json.loads(json.dumps(raw));result=normalize_features(raw);self.assertEqual(raw,original)
  self.assertEqual(result['Visite virtuelle'],'https://tour.example.test/one');self.assertNotIn('Barème des honoraires',result);self.assertNotIn('Logo secondaire',result)


 def test_floor_display_contains_real_floor_information_and_is_not_blindly_removed(self):
  result=normalize_features({'Étage de votre bien':'1','floor_display':'Étage 1/4'})
  self.assertEqual(result['Étage'],'1er étage sur 4')
  self.assertEqual(normalize_features({'floor_display':'RDC/3'})['Étage'],'Rez-de-chaussée sur 3')
