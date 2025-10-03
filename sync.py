
import json
from qgis.core import (
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsProject,
    QgsField,
    QgsFields,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateTransformContext,
    NULL,
    QgsPointXY,
    QgsWkbTypes,
    QgsPoint,
    QgsPointXY,
    QgsEditorWidgetSetup,
)
from qgis.PyQt.QtCore import QMetaType, QDate, QVariant
from qgis.PyQt.QtWidgets import QMessageBox


def configure_dropdown(layer, field_name, options):
    # Ensure field exists
    field_index = layer.fields().indexOf(field_name)
    if field_index == -1:
        layer.dataProvider().addAttributes([QgsField(field_name, QVariant.String)])
        layer.updateFields()
        field_index = layer.fields().indexOf(field_name)

    # Create value map configuration
    config = {
        'map': [{opt : opt} for opt in options],
        'AllowMulti': False,
        'AllowNull': False
    }
    
    # Configure editor widget
    widget_setup = QgsEditorWidgetSetup('ValueMap', config)
    layer.setEditorWidgetSetup(field_index, widget_setup)

def setup_field_widgets(read_only=None, dropdowns=None, layer=None, model=None):
    if read_only:
        for field_name in read_only:
            field_index = layer.fields().indexOf(field_name)
            if field_index != -1:
                edit_config = layer.editFormConfig()
                edit_config.setReadOnly(field_index, True)
                layer.setEditFormConfig(edit_config)

    if dropdowns and model in dropdowns:
        for field_name, options in dropdowns[model].items():
            configure_dropdown(layer, field_name, options)


def add_missing_fields(layer, fields):
    # Get existing fields from the layer
    existing_fields      = {field.name(): field for field in layer.fields()}
    existing_field_names = list(existing_fields.keys())
    new_field_names      = [field.name() for field in fields]

    # Compare and add missing fields
    fields_to_add = []
    for field in fields:
        if field.name() not in existing_field_names:
            fields_to_add.append(field)

    # Identify fields to remove
    fields_to_remove = []
    for name in existing_field_names:
        if name not in new_field_names:
            if any(unit in name.lower() for unit in ['ppm', 'pct', 'ppb', 'opt']):
                fields_to_remove.append(name)

    # Add new fields
    if fields_to_add:
        layer.dataProvider().addAttributes(fields_to_add)

    # Remove specified fields
    if fields_to_remove:
        layer.dataProvider().deleteAttributes([layer.fields().indexFromName(field) for field in fields_to_remove])

    # Update the layer's fields
    layer.updateFields()


    # # Access the edit form configuration
    # form_config = layer.editFormConfig()
    # field_index = layer.fields().indexFromName('natural_key')
    # layer.setEditorWidgetSetup(field_index, QgsEditorWidgetSetup('TextEdit', {'IsReadOnly': True}))
    # layer.commitChanges()



def round_coordinates(geom, precision=6):
    geom_type = geom.type()
    has_z = QgsWkbTypes.hasZ(geom.wkbType())  # Check if input has Z dimension
    
    if geom_type == QgsWkbTypes.PointGeometry:
        if geom.isMultipart():
            points = geom.asMultiPoint()
            rounded_points = []
            for pt in points:
                if has_z:
                    rounded_points.append(QgsPointXY(round(pt.x(), precision), 
                                                    round(pt.y(), precision)))
                else:
                    rounded_points.append(QgsPointXY(round(pt.x(), precision), 
                                                    round(pt.y(), precision)))
            geom = QgsGeometry.fromMultiPointXY(rounded_points)
            if has_z:
                # Reapply Z values if needed (not typical for points in this context)
                pass  # Z is lost here; handle via layer if needed
            return geom
        else:
            point = geom.asPoint()
            rounded_point = QgsPointXY(round(point.x(), precision), 
                                      round(point.y(), precision))
            geom = QgsGeometry.fromPointXY(rounded_point)
            if has_z:
                pass  # Z is lost here; handle via layer if needed
            return geom
    
    elif geom_type == QgsWkbTypes.LineGeometry:
        if geom.isMultipart():
            lines = geom.asMultiPolyline()
            rounded_lines = []
            for line in lines:
                if has_z:
                    rounded_line = [QgsPointXY(round(pt.x(), precision), 
                                              round(pt.y(), precision)) 
                                   for pt in line]
                else:
                    rounded_line = [QgsPointXY(round(pt.x(), precision), 
                                              round(pt.y(), precision)) 
                                   for pt in line]
                rounded_lines.append(rounded_line)
            geom = QgsGeometry.fromMultiPolylineXY(rounded_lines)
            if has_z:
                pass  # Z is lost here; handle via layer if needed
            return geom
        else:
            line = geom.asPolyline()
            if has_z:
                rounded_line = [QgsPointXY(round(pt.x(), precision), 
                                          round(pt.y(), precision)) 
                               for pt in line]
            else:
                rounded_line = [QgsPointXY(round(pt.x(), precision), 
                                          round(pt.y(), precision)) 
                               for pt in line]
            geom = QgsGeometry.fromPolylineXY(rounded_line)
            if has_z:
                pass  # Z is lost here; handle via layer if needed
            return geom
    
    elif geom_type == QgsWkbTypes.PolygonGeometry:
        if geom.isMultipart():
            parts = geom.asMultiPolygon()
            rounded_parts = []
            z_values = []  # Store Z values if needed
            for part in parts:
                rounded_part = []
                for ring in part:
                    if has_z:
                        rounded_ring = []
                        z_ring = []
                        for pt in ring:
                            rounded_ring.append(QgsPointXY(round(pt.x(), precision), 
                                                          round(pt.y(), precision)))
                            z_ring.append(round(pt.z(), precision))
                        rounded_part.append(rounded_ring)
                        z_values.append(z_ring)
                    else:
                        rounded_ring = [QgsPointXY(round(pt.x(), precision), 
                                                  round(pt.y(), precision)) 
                                       for pt in ring]
                        rounded_part.append(rounded_ring)
                rounded_parts.append(rounded_part)
            geom = QgsGeometry.fromMultiPolygonXY(rounded_parts)
            if has_z:
                # Convert to Z geometry and reapply Z values
                geom.convertToType(QgsWkbTypes.PolygonGeometry, True)  # Convert to MultiPolygonZ
                # Reapply Z values (requires iterating over the geometry)
                geom_with_z = geom.constGet()
                for i, part in enumerate(geom_with_z):
                    for j, ring in enumerate(part):
                        for k, vertex in enumerate(ring):
                            geom_with_z.setZAt(i, j, k, z_values[i][j][k])
            return geom
        else:
            part = geom.asPolygon()
            rounded_part = []
            z_values = []  # Store Z values if needed
            for ring in part:
                if has_z:
                    rounded_ring = []
                    z_ring = []
                    for pt in ring:
                        rounded_ring.append(QgsPointXY(round(pt.x(), precision), 
                                                      round(pt.y(), precision)))
                        z_ring.append(round(pt.z(), precision))
                    rounded_part.append(rounded_ring)
                    z_values.append(z_ring)
                else:
                    rounded_ring = [QgsPointXY(round(pt.x(), precision), 
                                              round(pt.y(), precision)) 
                                   for pt in ring]
                    rounded_part.append(rounded_ring)
            geom = QgsGeometry.fromPolygonXY(rounded_part)
            if has_z:
                # Convert to Z geometry and reapply Z values
                geom.convertToType(QgsWkbTypes.PolygonGeometry, True)  # Convert to PolygonZ
                geom_with_z = geom.constGet()
                for i, ring in enumerate(geom_with_z):
                    for j, vertex in enumerate(ring):
                        geom_with_z.setZAt(i, j, z_values[i][j])
            return geom
    
    else:
        return geom


def get_differences(item, existing_feature=None, max_length_images=0, max_length_docs=0, layer=None, new_layer=False, dir='pull'):
    message = ''
    # send this as an arg later so we don't have to keep calling it. same for the add_missing_fields function
    existing_fields = {field.name(): field for field in layer.fields()}

    # we will need to define the fields that are
    attributes = []
    urls       = item.get('image_urls')
    doc_urls   = item.get('document_urls')
    for field_name in existing_fields.keys():
        if '_nk' in field_name:
            attributes.append(json.dumps(item[field_name]))
        elif field_name in ['natural_key', 'methods']:
            attributes.append(json.dumps(item[field_name]))
        elif field_name in item:
            attributes.append(item[field_name])
        elif field_name == 'coords':
            pass
        elif 'image' in field_name:
            pass
        elif 'document' in field_name:
            pass
        # elif 'retain' in field_name:
        #     print(f'field name: {field_name}')
        #     print(f'retain value: {item[field_name]}')
        else:
            attributes.append(None)

    # add column for each image and document
    if max_length_images > 0:
        urls     = item.get('image_urls')
        for i in range(max_length_images):
            try:
                attributes.append(f"{urls[i]}")
            except IndexError:
                attributes.append(None)

    if max_length_docs > 0:
        doc_urls = item.get('document_urls')
        for i in range(max_length_docs):
            try:
                attributes.append(f"{doc_urls[i]}")
            except IndexError:
                attributes.append(None)

    if existing_feature:
        # message += f'Changes found with: {item["geodb_id"]}\n'
        change_detected = False
        fid = existing_feature.id()
        attr_map = {}

        # Map field names to indices
        field_indices = {field.name(): idx for idx, field in enumerate(layer.fields())}

        for field_name, index in field_indices.items():
            # for existing features, we don't want to update the fid, and don't have it in the list of attributes
            if field_name in ['fid', 'geodb_id']:
                continue

            # the attribute table does not have an fid entry at the beginning, so our indices are off by one
            existing_value = existing_feature[field_name]

            # special case for images and documents
            if 'image' in field_name:
                index = int(field_name.split('_')[-1]) -1 # we start counting fields at 1, not 0
                try:
                    value = item.get('image_urls')[index]
                except IndexError:
                    value = None
            elif 'document' in field_name:
                index = int(field_name.split('_')[-1]) -1 # we start counting fields at 1, not 0
                try:
                    value = item.get('document_urls')[index]
                except IndexError:
                    value = None
            else:
                value = item.get(field_name)

            # Skip if comparing NULL and None
            if (existing_value == NULL or existing_value is None) and value is None:
                continue

            # we don't want to adjust natural keys now. 
            if 'nk' in field_name:
                continue

            if field_name in ['methods']:
                continue


            # Handle date fields
            elif 'date' in field_name:# in ['date_staked', 'date_created']:
                if value:  # Only process if value exists
                    qdate = QDate.fromString(value, 'yyyy-MM-dd')
                    if existing_value != qdate:
                        if dir == 'push':
                            message += f'geodb_id: {item["geodb_id"]} will have {field_name} updated in geodb.io database: {qdate} -> {existing_value}\n'
                        else:
                            message += f'Updating geodb_id: {item["geodb_id"]} {field_name}: {existing_value} -> {qdate}\n'
                            attr_map[field_indices[field_name]] = qdate
                            change_detected = True
            else:
                # print(f'existing_value: {existing_value} and value: {value}')
                if str(existing_value) != str(value):
                    change_detected = True
                    if dir == 'push':
                        if 'image' in field_name or 'document' in field_name:
                            change_detected = False
                            continue
                        # natural key seems to change every time due to spacing between words, and we dont update it. no need to inform the user. 
                        if field_name != 'natural_key':
                            message += f'geodb_id: {item["geodb_id"]} will have {field_name} updated in geodb.io database: {value} -> {existing_value}\n'
                    else:
                        if field_name != 'natural_key':
                            message += f'Updating geodb_id: {item["geodb_id"]} {field_name}: {existing_value} -> {value}\n'
                        attr_map[field_indices[field_name]] = value
                    

        # Round the coordinates in existing_geom and item['coords']
        if 'coords' in item:
            existing_geom = existing_feature.geometry()
            item_geom = QgsGeometry.fromWkt(item['coords'])

            try:
                existing_geom_rounded = round_coordinates(existing_geom)
            except:
                existing_geom_rounded = None
            item_geom_rounded = round_coordinates(item_geom)

            if existing_geom_rounded is not None:
                if existing_geom_rounded.asWkt() != item_geom_rounded.asWkt():
                    change_detected = True
                    if dir == 'push':
                        message += 'geometry changed\n'
                    else:
                        message += 'Updating geometry\n'
                        existing_feature.setGeometry(item_geom_rounded)
                        layer.changeGeometry(fid, existing_feature.geometry())
                    

        # if change_detected is False:
        #     substring_to_remove = f'Changes found with: {item["geodb_id"]}\n'
        #     message             = message.replace(f"{substring_to_remove}", "")
        #     message             = message.rstrip('\r\n')

        # Apply attribute changes if any
        if dir == 'pull':
            if attr_map:
                layer.dataProvider().changeAttributeValues({fid: attr_map})

    else:
        if dir == 'pull':
            # Add new feature
            new_feature = QgsFeature()
            new_feature.setAttributes(attributes) 
            # print('attributes:', attributes)
            if 'coords' in item:
                new_feature.setGeometry(QgsGeometry.fromWkt(item['coords']))
            layer.addFeature(new_feature)
    # print(repr(message))
    if message == '':
        return None
    return message




def save_model(dlg=None, data=None, model=None, dir='pull'):

    field_defs = data.get('field_defs')
    
    # Define the mapping dictionary
    field_type_mapping = {
    'QString': QMetaType.Type.QString,
    'QDate'  : QMetaType.Type.QDate,
    'QInt'   : QMetaType.Type.Int,
    'QDouble': QMetaType.Type.Double,
    'QBool'  : QMetaType.Type.Bool,
    # Add other mappings as needed
    }

    # Convert the field definitions from strings to actual classes
    for field_name, field_type in field_defs.items():
        field_defs[field_name] = field_type_mapping[field_type]

    q_project          = QgsProject.instance()
    geopackage_path    = q_project.readEntry("geodb_vars", "gpkg_filepath")[0]

    if 'image_urls' in data.get('serialized_data')[0]:# data.get('serialized_data')[0]:
        max_length_images = max(len(item.get('image_urls', [])) for item in data['serialized_data'])
    else:
        max_length_images = 0
    if 'document_urls' in data.get('serialized_data')[0]:
        max_length_docs   = max(len(item.get('document_urls', [])) for item in data['serialized_data'])
    else:
        max_length_docs = 0

    # defines the fields we are going to need, whether or not we are creating a new layer
    fields = QgsFields()
    for field_name, field_type in field_defs.items():
        fields.append(QgsField(field_name, field_type))



    if 'image_urls' in data.get('serialized_data')[0]:
        for i in range(max_length_images):
            fields.append(QgsField(f'image_{i+1}', QMetaType.Type.QString))
    if 'document_urls' in data.get('serialized_data')[0]:
        for i in range(max_length_docs):
            fields.append(QgsField(f'document_{i+1}', QMetaType.Type.QString))

    
    # Check for existing layer already loaded into the project
    existing_layers = QgsProject.instance().mapLayersByName(model)
    if existing_layers and existing_layers[0].source().split('|')[0] == geopackage_path:
        layer = existing_layers[0]
    else:  
        # if not, try to load the layer from the GeoPackage
        layer = QgsVectorLayer(f"{geopackage_path}|layername={model}", model, "ogr")

    # check the changes to be made if layer is valid
    if layer.isValid():
        layer.startEditing()
        # Add the layer to the project if it's not already there
        if not QgsProject.instance().mapLayersByName(model):
            QgsProject.instance().addMapLayer(layer)
        #Add more image and document fields if needed
        add_missing_fields(layer, fields)



        setup_field_widgets(read_only=data.get('read_only'), dropdowns=data.get('dropdowns'), layer=layer, model=model)
        # read_only_fields = data.get('read_only')
        # if read_only_fields:
        #     for field_name in read_only_fields:
        #         field_index = layer.fields().indexOf(field_name)
        #         if field_index != -1:
        #             edit_config = layer.editFormConfig()
        #             edit_config.setReadOnly(field_index, True)
        #             layer.setEditFormConfig(edit_config)


        # dropdowns = data.get('dropdowns')
        # if dropdowns[model]:
        #     for field_name, options in dropdowns[model].items():
        #         configure_dropdown(layer, field_name, options)


        #clear the message box before adding new messages
        getattr(dlg, dir+'_message').clear()
        changes_detected = False

        # this only checks the data coming from the server, so it won't see new features that have been added to the layer
        for item in data['serialized_data']:
            existing_features = layer.getFeatures(f"geodb_id = {item['geodb_id']}")
            existing_feature  = next(existing_features, None)

            message           = get_differences(item=item,
                                                existing_feature=existing_feature,
                                                max_length_images=max_length_images,
                                                max_length_docs=max_length_docs,
                                                layer=layer,
                                                new_layer=False,
                                                dir=dir)
            # add the changes to the message box
            if message is not None:
                getattr(dlg, dir+'_message').append(message)
                changes_detected = True
                
            else:
                message = ''

        if dir == 'push':
            for item in layer.getFeatures():
                geodb_id = item['geodb_id']
                if geodb_id is None or geodb_id == '' or geodb_id == NULL:
                    if 'bhid_name' in item:
                        if 'depth_at' in item:
                            message += f'Adding row to {item["bhid_name"]} with depth_at {item["depth_at"]} to server\n'
                        elif 'depth_from' in item:
                            message += f'Adding row to {item["bhid_name"]} with depth_from {item["depth_from"]} to server\n'
                    else:
                        message += f'Adding row {item["name"]} to server\n'
                    if message != '':
                        getattr(dlg, dir+'_message').append(message)

        # Show message box to accept or decline changes
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Question)
        msg_box.setWindowTitle("Confirm Changes")

        if changes_detected is True:
            if dir == 'push':
                msg_box.setText("Please review the changes that will be pushed to the geodb.io")
            else:
                msg_box.setText("Changes have been applied as edits to the layer. To save them, save the layer.")

            msg_box.setStandardButtons(QMessageBox.Ok)
            response = msg_box.exec_()
            return "Changes detected."
        
        else:
            msg_box.setText("No changes detected.")
            msg_box.setStandardButtons(QMessageBox.Ok)
            response = msg_box.exec_()
            return "No changes detected."
        



    if not layer.isValid():
        # check if pushing, if so return a message that the layer is not valid
        if dir == 'push':
            QMessageBox.critical(dlg, 'Error', 'Layer is invalid.')
            return

        # Create a new layer
        geom_type = data.get('geom_type')
        # create a new memory layer if we weren't able to load the layer from the GeoPackage
        layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:4326", model, "memory")
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

        # set up read only and dropdowns
        setup_field_widgets(read_only=data.get('read_only'), dropdowns=data.get('dropdowns'), layer=layer, model=model)
        #  make geodb_id field not editable
        # config      = layer.editFormConfig()
        # field_index = layer.fields().indexOf('geodb_id')
        # config.setReadOnly(field_index, True)
        # layer.setEditFormConfig(config)

        
        # Add the layer to the project if it's not already there
        if not QgsProject.instance().mapLayersByName(model):
            QgsProject.instance().addMapLayer(layer)

        # Apply changes to the layer
        layer.startEditing()
        for item in data['serialized_data']:
            message = get_differences(item=item,
                                      max_length_images=max_length_images,
                                      max_length_docs=max_length_docs,
                                      layer=layer,
                                      new_layer=True)
            # add the changes to the message box
            getattr(dlg, dir+'_message').append(message)

        layer.commitChanges()
        layer.triggerRepaint()

        if layer.dataProvider().name() == "memory":
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = layer.name()

            _writer = QgsVectorFileWriter.writeAsVectorFormatV3(layer, geopackage_path, QgsCoordinateTransformContext(), options)

            # Update the layer's data source to point to the new GeoPackage
            layer.setDataSource(geopackage_path + f'|layername={layer.name()}', layer.name(), 'ogr')

            # msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            # msg_box.setDefaultButton(QMessageBox.No)
            # if response == QMessageBox.Yes:
            #     # Apply attribute changes if any
            #     layer.commitChanges()
            #     layer.triggerRepaint()
            #     return "Changes applied."
            # else:
            #     layer.rollBack()
            #     return "Changes declined."