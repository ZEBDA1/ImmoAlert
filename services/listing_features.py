"""Human-facing property characteristics. Raw portal data remains unchanged."""
from __future__ import annotations

from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PARIS=ZoneInfo('Europe/Paris')


def _key(value):
    value=unicodedata.normalize('NFKD',str(value))
    return re.sub(r'[^a-z0-9]','', ''.join(c for c in value if not unicodedata.combining(c)).lower())


# Aliases express business meaning, never inferred numeric portal taxonomies.
_GROUPS={
 'Date de publication':('datePosted','publication_date','published_at','date de publication','date publication'),
 'Dernière mise à jour':('updated_at','dateModified','date de mise à jour'),
 'Disponibilité':('availableFrom','available_from','disponibilité','disponible à partir de'),
 'Type de bien':('property_type','real_estate_type','type de bien'),
 'Nature du bien':('nature du bien',),
 'Location meublée':('furnished','meublé','ce bien est','furnishing'),
 'Surface habitable':('surface habitable','living_area','habitable_surface','floorSize','square'),
 'Surface':('surface','area'),
 'Surface du terrain':('land_area','terrain','surface totale du terrain','plot_area'),
 'Pièces':('pièce','pièces','nombre de pièces','rooms','numberOfRooms','rooms_count'),
 'Chambres':('chambre','chambres','nombre de chambres','bedrooms','numberOfBedrooms','bedrooms_count'),
 'Salles de bain':('salle de bain','salles de bain','nombre de salles de bain','bathrooms','numberOfBathroomsTotal'),
 'Salles d’eau':("salle d'eau","salles d'eau","nombre de salles d'eau",'shower_rooms'),
 'Étage':('floor','floorLevel','floor_number','étage','étage de votre bien',"étage de l'appartement",'floor_display'),
 'Étages de l’immeuble':('numberOfFloors','building_floors','nombre d’étages dans l’immeuble'),
 'Ascenseur':('elevator','lift','ascenseur'),
 'Dernier étage':('top_floor','dernier étage'),
 'Chambre au rez-de-chaussée':('chambre au RDC','bedroom_ground_floor'),
 'Type de chauffage':('heating_type','type de chauffage','heating_system'),
 'Énergie du chauffage':('heating_mode','heating_energy','mode de chauffage','énergie du chauffage'),
 'Chauffage':('chauffage','heating'),
 'Cuisine':('type cuisine','kitchen_type','cuisine'),
 'Places de parking':('parking_spaces','parking_count','places de parking'),
 'Type de stationnement':('type stationnement','parking_type'),
 'Stationnement':('parking','stationnement'),
 'Année de construction':('yearBuilt','construction_year','année de construction'),
 'Exposition':('orientation','exposure','exposition'),
 'État du bien':('condition','property_condition','état du bien'),
 'Équipements':('amenities','equipment','équipements','caractéristiques'),
 'Espaces extérieurs':('extérieur','outdoor_spaces'),
 'Balcon':('balcony','balcon'), 'Terrasse':('terrace','terrasse'),
 'Balcon / Terrasse':('balcon / terrasse',),
 'Jardin':('garden','jardin'), 'Cave':('cellar','cave'),
 'Climatisation':('air_conditioning','climatisation'),
 'Animaux acceptés':('petsAllowed','pets_allowed','animaux acceptés'),
 'Accès adapté aux personnes à mobilité réduite':('wheelchair_access','accès PMR'),
 'Classe énergie (DPE)':('DPE','energyClass','energy_class','classe énergie','classe énergétique'),
 'Classe climat (GES)':('GES','ges_class','classe climat'),
 'Diagnostic énergétique':('diagnostic énergétique',),
 'Loyer mensuel':('loyer','rent','monthly_rent'),
 'Loyer mensuel hors charges':('loyer mensuel (hors charges)','rent_excluding_charges'),
 'Loyer de référence majoré':('loyer de référence majoré',),
 'Loyer précédent annoncé':('old_price','previous_rent'),
 'Charges locatives':('charges','charges locatives','dont charges','rental_charges'),
 'Charges comprises':('charges incluses','charges comprises','charges_included'),
 'Détail des charges':('détail des charges',),
 'Dépôt de garantie':('deposit','security_deposit','dépôt de garantie'),
 'Honoraires de location':('honoraires','fees','agency_fees','montant des honoraires'),
 'Honoraires à la charge de':('charges honoraires','fees_payer'),
 'Frais d’état des lieux':("frais d'état des lieux",'inventory_fees'),
 'Prix hors honoraires':('prix hors honoraires',),
 'Charges annuelles de copropriété':('charges annuelles de copropriété',),
 'Dépenses énergétiques annuelles minimales':('montant annuel énergétique minimum',),
 'Dépenses énergétiques annuelles maximales':('montant annuel énergétique maximum',),
 'Encadrement des loyers':("bien soumis à l'encadrement des loyers",'rent_control'),
 'Référence de l’annonce':('référence','reference','ad_reference'),
 'Annonceur':('store_name','advertiser_name'), 'Agence':('agency_name','agence'),
 'Mandat':('mandate_type','type de mandat','mandat'),
 'Type de transaction':('lease_type','transaction_type'),
 'Visite virtuelle':('virtual_tour','visite virtuelle'),
 'Vue de la rue':('street_view_url',),
 'Barème des honoraires':('pro_rates_link','barème des honoraires'),
 'Adresse publiée':('adresse publiée',),
}
ALIASES={_key(alias):label for label,aliases in _GROUPS.items() for alias in (label,*aliases)}
INTERNAL={_key(key) for key in ('activity_sector','external_ad_id','district_id','district_visibility','district_type_id','district_resolution_type','has_visibility_option','visibility_option_products','store_logo','online_store_id','is_import','rating_score','rating_count','profile_picture_url','id','uuid','source','description','body','title','url','image','logo','tracking','metadata')}
BOOLEAN={'Location meublée','Charges comprises','Ascenseur','Dernier étage','Chambre au rez-de-chaussée','Balcon','Terrasse','Jardin','Cave','Climatisation','Animaux acceptés','Accès adapté aux personnes à mobilité réduite','Encadrement des loyers','Stationnement'}
MONEY={'Loyer mensuel','Loyer mensuel hors charges','Loyer de référence majoré','Loyer précédent annoncé','Charges locatives','Dépôt de garantie','Honoraires de location','Frais d’état des lieux','Prix hors honoraires','Charges annuelles de copropriété','Dépenses énergétiques annuelles minimales','Dépenses énergétiques annuelles maximales'}
LINKS={'Visite virtuelle','Vue de la rue','Barème des honoraires'}
COUNTS={'Pièces','Chambres','Salles de bain','Salles d’eau','Places de parking','Étages de l’immeuble'}
ENUMS={
 'Type de bien':{'apartment':'Appartement','flat':'Appartement','house':'Maison','studio':'Studio','villa':'Villa'},
 'Type de chauffage':{'individual':'Individuel','collective':'Collectif','central':'Central'},
 'Énergie du chauffage':{'electric':'Électrique','electricity':'Électrique','gas':'Gaz','fuel':'Fioul','wood':'Bois','other':'Autre'},
 'Mandat':{'simple':'Simple','exclusive':'Exclusif','semiexclusive':'Semi-exclusif'},
 'Type de transaction':{'rent':'Location','rental':'Location','sale':'Vente','buy':'Vente'},
 'Honoraires à la charge de':{'buyer':'Acquéreur','seller':'Vendeur','tenant':'Locataire','lessor':'Bailleur','owner':'Propriétaire'},
}


def format_date(value,*,include_time=True):
    """Only timestamps carry a time; a date without a time stays a date."""
    if isinstance(value,datetime):parsed=value
    elif isinstance(value,date):return value.strftime('%d/%m/%Y')
    elif isinstance(value,str):
        value=value.strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            try:return date.fromisoformat(value).strftime('%d/%m/%Y')
            except ValueError:return None
        try:parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:
            for pattern in ('%d/%m/%Y %H:%M:%S','%d/%m/%Y %H:%M','%d/%m/%Y'):
                try:
                    parsed=datetime.strptime(value,pattern)
                    if pattern=='%d/%m/%Y':return parsed.strftime('%d/%m/%Y')
                    break
                except ValueError:continue
            else:return None
    else:return None
    if parsed.tzinfo is not None:parsed=parsed.astimezone(PARIS)
    return parsed.strftime('%d/%m/%Y %H:%M:%S' if include_time else '%d/%m/%Y')


def _number(value):
    text=re.sub(r'\s','',str(value)).replace('€','').replace('/an','')
    if ',' in text and '.' in text:
        text=text.replace('.','').replace(',','.') if text.rfind(',')>text.rfind('.') else text.replace(',','')
    else:text=text.replace(',','.')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):return None
    try:
        result=Decimal(text)
        return result if result.is_finite() and abs(result)<Decimal('1000000000000') else None
    except InvalidOperation:return None


def _decimal(value,*,money=False):
    text=f'{value:,.2f}' if money and value!=value.to_integral_value() else f'{value:,.0f}' if value==value.to_integral_value() else format(value.normalize(),'f')
    return text.replace(',',' ').replace('.',',')


def _value(label,value):
    if value is None or not isinstance(value,(str,int,float,bool)):return None
    if isinstance(value,float) and not math.isfinite(value):return None
    text=re.sub(r'\s+',' ',str(value)).strip()
    if not text or text.casefold() in {'null','none','undefined','nan','n/a','nc','non renseigné','non communiqué','-'}:return None
    if label in LINKS:
        try:
            parsed=urlsplit(text)
            return text if parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password else None
        except ValueError:return None
    if re.match(r'^(?:https?://|\{\s*"|\[\s*["{])',text,re.I):return None
    if label in {'Date de publication','Dernière mise à jour','Disponibilité'}:
        rendered=format_date(text,include_time=label!='Disponibilité')
        if rendered:return rendered
        if re.match(r'^\d{4}-\d{2}-\d{2}',text):return None
        return 'Immédiate' if _key(text) in {'now','immediate','immediatement'} else text
    token=_key(text)
    if label=='Étage' and (isinstance(value,bool) or token in {'true','false'}):return None
    if label in BOOLEAN:
        if value is True or token in {'true','yes','oui','1','meuble','furnished'}:return 'Oui'
        if value is False or token in {'false','no','non','0','nonmeuble','unfurnished'}:return 'Non'
    if isinstance(value,bool) or token in {'true','false'}:return 'Oui' if token=='true' else 'Non'
    if label in ENUMS:
        if re.fullmatch(r'\d+',text):return None  # an undocumented taxonomy is not a human description
        return ENUMS[label].get(token) or (None if '_' in text else text)
    if label=='Étage':
        text=re.sub(r'^(?:étage|floor)\s*','',text,flags=re.I)
        ground=re.fullmatch(r'(?:rdc|rez-de-chaussée|ground floor)(?:\s*/\s*(\d+))?',text,flags=re.I)
        if ground:return 'Rez-de-chaussée'+(f' sur {ground[1]}' if ground[1] else '')
        if token in {'rdc','groundfloor','rezdechaussee'}:return 'Rez-de-chaussée'
        match=re.fullmatch(r'(-?\d+)(?:\s*/\s*(\d+))?',text)
        if match:
            floor=int(match[1]);suffix='er' if abs(floor)==1 else 'e'
            result='Rez-de-chaussée' if floor==0 else f'{abs(floor)}{suffix} sous-sol' if floor<0 else f'{floor}{suffix} étage'
            return result+(f' sur {match[2]}' if match[2] else '')
    if label in MONEY:
        amount=_number(text)
        if amount is not None:
            annual=label in {'Charges annuelles de copropriété','Dépenses énergétiques annuelles minimales','Dépenses énergétiques annuelles maximales'}
            return _decimal(amount,money=True)+(' € / an' if annual else ' €')
    if label in {'Surface','Surface habitable','Surface du terrain'}:
        amount=_number(re.sub(r'\s*m(?:²|2)\s*$','',text,flags=re.I))
        if amount is not None:return _decimal(amount)+' m²'
    if label in COUNTS:
        amount=_number(re.sub(r'\s*(?:ch\.?|chambres?|pièces?|places?)\s*$','',text,flags=re.I))
        if amount is not None:return 'Aucune' if label=='Places de parking' and amount==0 else _decimal(amount)
    if label in {'Classe énergie (DPE)','Classe climat (GES)'} and len(text)==1:return text.upper() if text.upper() in 'ABCDEFG' else None
    return text


def normalize_features(raw):
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except (ValueError,TypeError):return {}
    if not isinstance(raw,dict):return {}
    output={};quality={}
    for raw_label,value in raw.items():
        if not isinstance(raw_label,str):continue
        label=re.sub(r'\s+',' ',raw_label).strip(' :')
        token=_key(label)
        if not label or token in INTERNAL:continue
        canonical=ALIASES.get(token)
        if canonical is None:
            # Preserve readable labels, not opaque import keys or identifiers.
            if '_' in label or re.search(r'[a-z][A-Z]|(?:^|\s)(?:id|uuid)(?:$|\s)',label) or not label[0].isupper():continue
            canonical=label
        rendered=_value(canonical,value)
        if rendered is None:continue
        # Prefer an explicit presentation label over a raw-key fallback, then
        # keep the first observation rather than silently replacing a conflict.
        priority=2 if ' ' in label or label in {'DPE','GES'} else 1
        if token=='floordisplay' and re.match(r'^(?:étage\s|rdc|rez-de-chaussée)',str(value),re.I):priority=3
        if canonical not in output or priority>quality[canonical]:output[canonical]=rendered;quality[canonical]=priority
    return output
