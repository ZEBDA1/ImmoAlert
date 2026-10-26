import json
import unittest
from scraper.aviv import search_rows
from scraper.quality import normalize_listing

class AvivSearchTests(unittest.TestCase):
    def page(self,rows,identifiers=None):
        data={'data':{'classified-serp-data':{'classifieds':identifiers or list(rows),'classifiedsData':rows}}}
        return '<script id="__UFRN_FETCHER__">window["__UFRN_FETCHER__"]=JSON.parse('+json.dumps(json.dumps(data))+');</script>'
    def item(self,source='logicimmo',**values):
        row={'id':'native-1','url':'https://www.logic-immo.com/detail-annonce/native-1','rawData':{'distributionType':'RENT'},'location':{'address':{'city':'Rouen','zipCode':'76000'}},'hardFacts':{'title':'Appartement à louer','price':{'formatted':'850 €/mois'},'facts':[{'type':'livingSpace','value':'45 m²'},{'type':'numberOfRooms','value':'2 pièces'}]},'mainDescription':{'headline':'Appartement T2','description':'Pièces lumineuses et cuisine aménagée.'},'gallery':{'images':[{'url':'https://images.test/home.jpg'}]}}
        row.update(values);return row
    def test_native_rental_facts_are_read_without_visiting_detail_pages(self):
        rows=search_rows(self.page({'one':self.item()}),'logicimmo')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].price,850);self.assertEqual(rows[0].surface_m2,45);self.assertEqual(rows[0].rooms,2);self.assertEqual(rows[0].location,'Rouen 76000');self.assertIsNotNone(normalize_listing(rows[0]))
    def test_sales_external_urls_and_suggested_results_are_excluded(self):
        rows={'rental':self.item(),'sale':self.item(rawData={'distributionType':'BUY'}),'external':self.item(url='https://evil.test/item'),'suggested':self.item()}
        self.assertEqual(len(search_rows(self.page(rows,['rental','sale','external']),'logicimmo')),1)
    def test_payload_changes_return_no_guessed_listing(self):
        self.assertEqual(search_rows('<script id="__UFRN_FETCHER__">unexpected()</script>','logicimmo'),[])
