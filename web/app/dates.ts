const listingDate=new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
export function formatDateTime(value:string){
 const date=new Date(value);if(!Number.isFinite(date.getTime()))return 'Date non précisée';
 const parts=Object.fromEntries(listingDate.formatToParts(date).map(part=>[part.type,part.value]));
 return `${parts.day}/${parts.month}/${parts.year} ${parts.hour}:${parts.minute}:${parts.second}`;
}
